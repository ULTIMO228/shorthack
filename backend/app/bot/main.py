"""Точка входа Telegram-бота поддержки МИСИС (фича 003).

Запуск:
    python -m app.bot.main
"""

import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from app.bot.core_api import CoreApiClient
from app.bot.tg import TelegramClient

try:
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
    else:
        load_dotenv()
except ImportError:
    pass

try:
    from app.bot.handlers import dispatch_update
except ImportError:
    dispatch_update = None

logger = logging.getLogger("bot.main")

DEFAULT_OFFSET_FILE = Path(__file__).resolve().parent.parent.parent / ".bot_offset"


def load_offset(path: Path) -> int | None:
    """Чтение последнего сохранённого offset из файла."""
    try:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            if content.isdigit():
                return int(content)
    except Exception as exc:
        logger.warning("Не удалось прочитать offset из %s: %s", path, exc)
    return None


def save_offset(path: Path, offset: int) -> None:
    """Сохранение offset в файл."""
    try:
        path.write_text(str(offset), encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось сохранить offset в %s: %s", path, exc)


class BotRunner:
    """Оркестратор long polling бота и обработки входящих апдейтов."""

    def __init__(
        self,
        tg_client: TelegramClient,
        core_client: CoreApiClient | None = None,
        offset_file: Path | None = None,
    ):
        self.tg = tg_client
        self.core = core_client or CoreApiClient()
        self.offset_file = offset_file or DEFAULT_OFFSET_FILE
        self.offset: int | None = load_offset(self.offset_file)
        self.running = True
        self.backoff = 1.0

    def stop(self) -> None:
        """Остановка цикла polling."""
        self.running = False

    def process_update(self, update: dict[str, Any]) -> None:
        """Обработка одного update Telegram."""
        update_id = update.get("update_id")
        if dispatch_update is not None:
            dispatch_update(update, self.tg, self.core)
        else:
            logger.debug("Получен update %s (обработчик handlers еще не подключен)", update_id)

    def run_polling_step(self, timeout: int = 30) -> int:
        """Один шаг получения и обработки updates. Возвращает количество обработанных updates."""
        try:
            updates = self.tg.get_updates(
                offset=self.offset,
                timeout=timeout,
                allowed_updates=["message", "callback_query"],
            )
            # При успешном сетевом запросе сбрасываем backoff
            self.backoff = 1.0

            for u in updates:
                self.process_update(u)
                update_id = u.get("update_id")
                if update_id is not None:
                    self.offset = update_id + 1
                    save_offset(self.offset_file, self.offset)

            return len(updates)

        except Exception as exc:
            logger.warning("Ошибка при get_updates: %s. Ожидание %.1f с...", exc, self.backoff)
            # Экспоненциальный backoff от 1.0 до 60.0 секунд
            time.sleep(self.backoff)
            self.backoff = min(self.backoff * 2.0, 60.0)
            return 0

    def run_outbox_step(self) -> int:
        """Один шаг опроса очереди исходящих уведомлений дежурному (US5 / T011).

        Возвращает количество успешно отправленных сообщений.
        """
        try:
            messages = self.core.get_pending_outbound()
        except Exception as exc:
            logger.warning("Ошибка получения outbound сообщений: %s", exc)
            return 0

        count = 0
        duty_chat_id_env = os.getenv("TG_DUTY_CHAT_ID")

        for msg in messages:
            msg_id = msg.get("id")
            if msg_id is None:
                continue

            chat_id = msg.get("chat_id")
            if not chat_id and duty_chat_id_env and duty_chat_id_env.strip().lstrip("-").isdigit():
                chat_id = int(duty_chat_id_env.strip())

            text = msg.get("text", "")
            if not chat_id or not text:
                self.core.ack_outbound(msg_id, "failed")
                continue

            try:
                self.tg.send_message(chat_id, text)
                self.core.ack_outbound(msg_id, "sent")
                count += 1
            except Exception as exc:
                logger.warning(
                    "Ошибка отправки outbound сообщения %s в чат %s: %s",
                    msg_id,
                    chat_id,
                    exc,
                )
                self.core.ack_outbound(msg_id, "failed")

        return count

    def run(self) -> None:
        """Главный бесконечный цикл long polling с безопасной остановкой."""
        try:
            me = self.tg.get_me()
            username = me.get("username", "unknown_bot")
        except Exception as exc:
            logger.warning("Не удалось получить get_me(): %s. Запуск без имени бота.", exc)
            username = "unknown"

        logger.info("polling as @%s", username)

        last_outbox_check = 0.0
        while self.running:
            try:
                now = time.time()
                if now - last_outbox_check >= 10.0:
                    self.run_outbox_step()
                    last_outbox_check = now

                self.run_polling_step(timeout=30)
            except KeyboardInterrupt:
                logger.info("Получен сигнал прерывания. Завершение работы...")
                break


def main() -> None:
    """Точка входа CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = os.getenv("TG_BOT_TOKEN", "").strip()
    if not token:
        logger.error("Переменная окружения TG_BOT_TOKEN не задана. Завершение.")
        sys.exit(1)

    tg_client = TelegramClient(token=token)
    core_client = CoreApiClient()
    runner = BotRunner(tg_client=tg_client, core_client=core_client)

    def _signal_handler(signum: int, _frame: Any) -> None:
        logger.info("Получен системный сигнал %s. Остановка...", signum)
        runner.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        runner.run()
    finally:
        tg_client.close()
        core_client.close()
        if runner.offset is not None:
            save_offset(runner.offset_file, runner.offset)
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()
