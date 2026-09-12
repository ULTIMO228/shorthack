# PAGESPEC — спека страниц прототипа (читать КАЖДОМУ агенту-странице)

## Контекст
Прототип веб-продукта «Помощник поддержки МИСИС» — ИИ-агент техподдержки университета.
Структура сайта: маркетинговый лендинг → вход (email МИСИС или гость) → приложение.
Дизайн утверждён: светлая тема, бренд `#0047FF`, чернильный navy `#0A1E64`, слабый liquid glass
(только шапка/борд/модалки), шрифт Golos Text + JetBrains Mono (цифры, тикеты, терминал),
только inline SVG-иконки (НИКАКИХ эмодзи), реалистичные русские тексты (см. данные ниже).

## Жёсткие правила
1. Каждая страница — ОТДЕЛЬНЫЙ html-файл в `frontend/prototype/`, подключает `<link rel="stylesheet" href="assets/design-system.css">`.
2. Шрифты: `<link href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">`.
3. Никаких эмодзи. Иконки — inline SVG, stroke 1.8, viewBox 0 0 24 24 (стиль lucide).
4. Фон каждой страницы: `<div class="bg-decor"></div>` сразу после `<body>`.
5. Контент — реалистичный русский (имена/номера из данных ниже), никаких lorem/John Doe.
6. Анимации: класс `anim` + inline `style="--i:N"` для stagger-входа (уже в CSS).
7. Скриншот-проверка: `"$CHROME" --headless --disable-gpu --hide-scrollbars --window-size=1440,1100 --screenshot=/tmp/<page>.png "file://<abs-путь>"` — посмотреть своими глазами, исправить визуальные косяки, повторить.
8. Страницы связываются ссылками (href на другие файлы прототипа), активный пункт навигации — класс `active`.
9. Блок `<main class="wrap" style="padding:28px 0 80px">` для страниц приложения.

## Карта страниц и навигация
- `index.html` — лендинг (продукт «что и как»)
- `login.html` — вход по email + «продолжить как гость»
- `app.html` — приложение: новое обращение + результат + чат (главный экран)
- `cabinet.html` — кабинет: мои обращения (статусы) + справки (каталог, заказы)
- `admin-queue.html` — очередь оператора
- `admin-ticket.html` — карточка эскалации
- `admin-certs.html` — заказы справок (оператор)
- `admin-tools.html` — тест-панель инструментов
- `admin-metrics.html` — метрики

Навигация приложения (шапка, для app/cabinet/admin-*):
Обращение→app.html · Кабинет→cabinet.html · АДМИНКА: Очередь→admin-queue.html · Эскалация→admin-ticket.html ·
Заказы→admin-certs.html · Инструменты→admin-tools.html · Метрики→admin-metrics.html

## Сниппет шапки приложения (скопировать, выставить active)
```html
<header class="header"><div class="header-in">
  <a class="logo" href="app.html"><div class="logo-mark">М</div><div class="logo-text"><b>Помощник поддержки</b><span>НИТУ МИСИС</span></div></a>
  <nav class="nav">
    <a class="nav-btn active" href="app.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>Обращение</a>
    <a class="nav-btn" href="cabinet.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>Кабинет</a>
    <span class="nav-label">Админка</span>
    <a class="nav-btn" href="admin-queue.html">…Очередь</a>
    <a class="nav-btn" href="admin-ticket.html">…Эскалация</a>
    <a class="nav-btn" href="admin-certs.html">…Заказы</a>
    <a class="nav-btn" href="admin-tools.html">…Инструменты</a>
    <a class="nav-btn" href="admin-metrics.html">…Метрики</a>
  </nav>
  <div class="header-right">
    <div class="chip-profile"><span class="chip-ava">ИИ</span><div><b>Иванов Иван</b><span>ИТ-24</span></div></div>
    <a class="btn btn-ghost btn-sm" href="login.html"><svg …>…</svg>Выйти</a>
  </div>
</div></header>
```
(иконки в nav: message-square, home, list, alert-circle, file-text, activity, bar-chart — inline SVG lucide-стиля; у кнопки «Выйти» icon log-out)

## Данные для контента (единые для всех страниц)
- Пользователь: Иванов Иван, группа ИТ-24, ivanov@edu.misis.ru. Оператор: Смирнова Ольга.
- Тикеты: SUP-2026-0012 (Wi-Fi MISIS-EDU, критично, ждёт ответа), SUP-2026-0007, SUP-2026-0003, SUP-2026-0001.
- Сервисы: Сайт МИСИС (misis.ru, real, 200 OK 183 мс), LMS (newlms, real, 200 OK 240 мс),
  Wi-Fi: MISIS-EDU (emulated, в сбое), MISIS-CORP (ok), MISIS-Guest (ok).
- Справки (5): Справка о выплатах / Справка-вызов / Справка для переболевших (медотвод, вакцинация) /
  Справка с места учёбы / Справка в военкомат. Цепочка статусов: Не обработана → Обрабатывается →
  Готова и ждёт выдачи → Забрана.
- Маршруты (RouteBadge): Авто-проверка (r-auto, иконка бот) / База знаний (r-kb, книга) /
  Заказ справки (r-cert, документ) / Оператор (r-op, человек).
- Приоритеты: Критично (b-crit) / Высокий (b-high) / Средний (b-med) / Низкий (b-low).
- Метрики: 62,5% без оператора · 4,8 сек реакция · 2 инцидента (1 активен) · 16 обращений · 3 эскалации · 5 заказов.
- Инцидент №2: Wi-Fi MISIS-EDU с 14:32, затронуто 3 обращения.
