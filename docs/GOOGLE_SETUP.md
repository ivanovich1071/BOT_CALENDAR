# Настройка Google Cloud: OAuth Client ID для календарей

> Выполняется ОДИН раз, под аккаунтом владельца проекта (сейчас — ваш,
> при передаче заказчику — его). Время: ~10 минут.

Цель: получить пару `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`, чтобы
сотрудники могли кнопкой «Подключить Google Calendar» дать системе доступ
к своим календарям (OAuth 2.0).

Проект: **gen-lang-client-0718795479** («Gemini Project new 08092026»).

---

## Шаг 1. Откройте проект

1. Перейдите: https://console.cloud.google.com
2. Сверху, рядом с логотипом — выпадающий список проектов. Выберите
   **Gemini Project new 08092026** (если не видите — «Новый проект» не нужен,
   воспользуйтесь поиском по ID `gen-lang-client-0718795479`).

## Шаг 2. Включите Google Calendar API

1. Меню ☰ → **APIs & Services → Library** (Библиотека).
2. В поиске: `Google Calendar API`.
3. Откройте карточку → кнопка **Enable** (Включить).

## Шаг 3. Настройте экран согласования (OAuth consent screen)

1. Меню ☰ → **APIs & Services → OAuth consent screen**
   (в новом интерфейсе: **Google Auth Platform → Branding**).
2. User Type: **External** (Внешний) → **Create**.
3. Заполните:
   - App name: например `Запись онлайн` (название увидят сотрудники при
     подключении календаря);
   - User support email: ваша почта;
   - Developer contact: ваша почта.
4. Дальше — **Audience / Test users**: нажмите **+ Add users** и добавьте
   email-адреса сотрудников, которые будут подключать календари
   (пока проект в статусе Testing, доступ имеют только они).
   Статус «Testing» для работы достаточен; при желании позже нажмите
   **Publish app**.

## Шаг 4. Создайте OAuth Client ID

1. Меню ☰ → **APIs & Services → Credentials**
   (новый интерфейс: **Google Auth Platform → Clients**).
2. **+ Create Credentials → OAuth client ID**.
3. Application type: **Web application**. Имя: `booking-platform`.
4. **Authorized redirect URIs** — добавьте ОБА адреса:
   - `http://localhost:8000/oauth/google/callback` (разработка)
   - `https://ВАШ-БУДУЩИЙ-ДОМЕН/oauth/google/callback` (можно добавить позже,
     когда появится домен)
5. **Create**.

## Шаг 5. Перенесите ключи в секреты

Появится окно с **Client ID** и **Client Secret**. Впишите их в `.env`:

```
GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
```

…или просто пришлите их мне в чат — я внесу и проверю вживую.

## Проверка

После заполнения `.env` я проверю: сформирую ссылку OAuth, пройду
«Подключить Google» тестовым сотрудником и создам пробное событие
в календаре. Если событие появилось — интеграция работает.

## Важно знать

- API-ключ «от гугл-календаря» (AIzaSyA5Wa…) для этой схемы НЕ используется:
  доступ к календарям пользователей даёт именно OAuth.
- Token'ы сотрудников хранятся в БД зашифрованными (Fernet), сотрудник
  никогда не видит клиент-секрет.
- Отозвать доступ можно в любой момент:
  https://myaccount.google.com/permissions
