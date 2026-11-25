# Assistant: a local AI agent for your Windows PC

Assistant is a chat agent that manages files and runs device commands on your computer.
It runs **locally** with [Ollama](https://ollama.com), so no API key is needed, and you can
use it from the terminal, a full-screen terminal app, or WhatsApp.

```
You:        create a folder called notes on my desktop
Assistant:  [tool] run_command(New-Item -ItemType Directory -Path ~/Desktop/notes)
            Done! The folder "notes" was created on your Desktop.

You:        what time is it?
Assistant:  The current time is 19:15.

You:        /search latest news from ESPRIT university
Assistant:  The latest news from ESPRIT... Source: https://www.esprit.tn/...
```

## What it can do

- **Files and folders:** list, find, open, create, copy, move, rename, delete
- **Apps:** launch Chrome, Notepad, and other programs
- **Device info:** time, date, disk space, battery, computer name, IP address
- **Web search:** only when the message starts with `/search` (see below)
- **Greetings:** small talk like "hello" or "thanks"

Anything else (recipes, poems, general knowledge...) is refused on purpose. The assistant is
a file and device helper, not a general chatbot.

## How it works

```
your message
    │
    ├── starts with "/search"? ──► web search agent (search.py) ──► Bing + web pages
    │
    └── otherwise ──► scope guard (guard.py) ──► file agent (assistant.py) ──► PowerShell
                      keeps only the in-scope part
```

| File | Role |
|---|---|
| `assistant.py` | The main agent loop and its system prompt. Also a plain terminal interface. |
| `guard.py` | Scope guard: a second model call that removes out-of-scope requests before the agent sees them. "Create pizza.txt with a pizza recipe" creates an **empty** file and refuses the recipe. |
| `tools.py` | The `run_command` tool. Runs PowerShell, blocks dangerous commands (format, shutdown...) and asks for confirmation before risky ones (delete, move, overwrite...). |
| `search.py` | `/search` mode: `web_search` and `fetch_page` tools built on [Scrapling](https://github.com/D4Vinci/Scrapling). |
| `app.py` | Full-screen terminal app (Textual) with saved sessions. |
| `whatsapp_bot.py` | WhatsApp interface (Meta WhatsApp Cloud API). |
| `db.py` | Stores chat sessions in a local SQLite file (`sessions.db`). |

## Setup

You need **Windows**, **Python 3.11+** and **Ollama**.

1. **Install Ollama** from <https://ollama.com/download>, then download the model:
   ```powershell
   ollama pull qwen3.5:4b
   ```
   You can use another model that supports tool calling: change `MODEL` at the top of `assistant.py`.

2. **Clone the project and install the dependencies:**
   ```powershell
   git clone https://github.com/cyrinekrichen/PromptProject.git
   cd PromptProject
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Check that Ollama works:**
   ```powershell
   python test.py
   ```
   It should print a short greeting from the model.

## Running it

**Plain terminal:**
```powershell
python assistant.py
```
Type `exit` to quit.

**Terminal app (recommended):**
```powershell
python app.py
```
It saves your sessions, so you can resume or rename them from the sidebar. Risky commands open
a Yes/No dialog. Type `/exit` to quit.

**WhatsApp (optional):** see [WhatsApp setup](#whatsapp-setup) below.

## Web search with `/search`

The assistant only goes on the internet when your message **starts with** `/search`:

| Message | What happens |
|---|---|
| `/search latest news from SMU university` | Web search ✅ |
| `search latest news from SMU university` | Refused (no web search) |
| `/search` | Shows how to use the command |

The agent searches Bing, opens the most relevant page, follows links like "News" or "Events"
when needed, and answers with the source URL. Results from **Tunisia** are preferred
when a name is ambiguous (for example "SMU" means South Mediterranean University).

## Safety

- Dangerous commands (formatting a disk, shutdown, registry edits...) are **blocked**.
- Risky commands (delete, move, rename, overwrite) **ask you first**.
- The assistant only reports what commands actually return; it doesn't invent file names.
- Still: it runs real commands on your PC. Read the confirmation prompts before saying yes.

## WhatsApp setup

This lets you chat with the assistant on your laptop from your phone.

1. Create an app at <https://developers.facebook.com> and add the **WhatsApp** product.
2. Copy `.env.example` to `.env` and fill in your values:
   ```
   WHATSAPP_TOKEN=...            # access token
   WHATSAPP_PHONE_NUMBER_ID=...  # from the WhatsApp > API Setup page
   WHATSAPP_VERIFY_TOKEN=...     # any secret string you choose
   WHATSAPP_APP_SECRET=...       # App settings > Basic
   ALLOWED_NUMBERS=216XXXXXXXX   # who may use the bot, no "+"
   ```
   **Never commit `.env`.** It is already in `.gitignore`.
3. Start the bot and expose it to the internet with [ngrok](https://ngrok.com):
   ```powershell
   python whatsapp_bot.py
   ngrok http 8000
   ```
4. In the Meta dashboard, set the webhook URL to `https://<your-ngrok-url>/webhook`, use your
   `WHATSAPP_VERIFY_TOKEN`, and subscribe to **messages**.

WhatsApp commands: `new`, `sessions`, `current`, `help`, and `/search <question>`.
Messages from numbers not in `ALLOWED_NUMBERS` are ignored.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ConnectionError` / connection refused | Ollama isn't running. Open the Ollama app or run `ollama serve`. |
| `model not found` | Run `ollama pull qwen3.5:4b`. |
| `No module named 'curl_cffi'` | Run `pip install "scrapling[fetchers]"`. |
| Answers are slow | Normal on a laptop without a GPU; `/search` is the slowest because it reads web pages. |
| Answers are cut off in `/search` | Your PC may be low on memory: lower `num_ctx` in `search_turn` in `assistant.py`. |
