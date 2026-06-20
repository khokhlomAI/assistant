#!/usr/bin/env python3
"""Personal AI assistant — Ollama (local), DeepSeek (direct), or OpenRouter."""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml


# ── Config ────────────────────────────────────────────

def load_config():
    path = Path("config.yml")
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f)
    return {}


def read_system_prompt():
    p = Path("system.md")
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "Ты ассистент. Отвечай по-русски, разговорно, без воды."


# ── LLM Providers ─────────────────────────────────────

def ollama_generate(host, model, system, user_text):
    resp = requests.post(
        f"{host}/api/generate",
        json={"model": model, "prompt": f"{system}\n\n{user_text}", "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _chat_completion(url, api_key, model, system, user_text):
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def openrouter_generate(api_key, model, system, user_text):
    return _chat_completion("https://openrouter.ai/api/v1/chat/completions", api_key, model, system, user_text)


def deepseek_generate(api_key, model, system, user_text):
    return _chat_completion("https://api.deepseek.com/v1/chat/completions", api_key, model, system, user_text)


# ── Action Parsing ────────────────────────────────────

def parse_action(output: str):
    """Extract action tag from the FIRST LINE of LLM output."""
    first_line = output.split("\n")[0].strip() if output else ""

    # [[note:text]]
    m = re.match(r"\[\[note:(.*?)\]\]", first_line)
    if m: return ("note", m.group(1).strip(), output)

    # [[wiki:Title\ncontent]]
    m = re.match(r"\[\[wiki:(.*?)\]\]", first_line)
    if m:
        inner = m.group(1).strip()
        lines = inner.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        return ("wiki", {"title": title, "content": body}, output)

    # [[opencode:cmd]]
    m = re.match(r"\[\[opencode:(.*?)\]\]", first_line)
    if m: return ("opencode", m.group(1).strip(), output)

    # [[prompt:goal]]
    m = re.match(r"\[\[prompt:(.*?)\]\]", first_line)
    if m: return ("prompt", m.group(1).strip(), output)

    # [[tz:project]]
    m = re.match(r"\[\[tz:(.*?)\]\]", first_line)
    if m: return ("tz", m.group(1).strip(), output)

    # [[remind:text]]
    m = re.match(r"\[\[remind:(.*?)\]\]", first_line)
    if m: return ("remind", m.group(1).strip(), output)

    return ("reply", output, output)


# ── Actions ───────────────────────────────────────────

def safe_filename(name):
    return re.sub(r'[/\\:*?"<>|]', "_", name)


def action_note(cfg, text):
    vault = Path(cfg["obsidian"]["vault"])
    sub = cfg["obsidian"].get("notes_subdir", "daily")
    d = vault / sub
    d.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H:%M")
    p = d / f"{today}.md"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n\n{text}\n")
    print(f"Заметка: {p}")


def action_wiki(cfg, data):
    vault = Path(cfg["obsidian"]["vault"])
    sub = cfg["obsidian"].get("wiki_subdir", "wiki")
    d = vault / sub
    d.mkdir(parents=True, exist_ok=True)
    title = data["title"] or datetime.now().strftime("%Y-%m-%d_%H-%M")
    content = data["content"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    p = d / f"{safe_filename(title)}.md"
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"---\ntitle: {title}\ndate: {now}\ntags: []\n---\n\n{content}\n")
    print(f"Вики: {p}")


def action_opencode(cfg, command):
    print(f"OpenCode: {command}")
    # SSH to VM if configured - placeholder for now
    host = cfg.get("opencode", {}).get("host", "")
    if host:
        user = cfg["opencode"].get("user", "")
        port = cfg["opencode"].get("port", 22)
        import subprocess
        subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no",
             f"{user}@{host}", "-p", str(port),
             f"tmux send-keys -t opencode '{command}' Enter"],
            check=False,
        )


def action_save(cfg, kind, name, output):
    vault = Path(cfg["obsidian"]["vault"])
    sub = cfg["obsidian"].get(f"{kind}_subdir", kind)
    d = vault / sub
    d.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    p = d / f"{date}-{safe_filename(name[:60])}.md"
    title_map = {"prompt": "Промпт", "tz": "Техническое задание"}
    title = title_map.get(kind, kind.upper())
    with open(p, "w", encoding="utf-8") as f:
        f.write(f"# {title}: {name}\n\n{output}\n")
    print(f"{title}: {p}")


def action_remind(cfg, text):
    vault = Path(cfg["obsidian"]["vault"])
    d = vault / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "reminders.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"- [ ] **{ts}** — {text}\n")
    print(f"Напоминание: {p}")


def execute_action(action_type, data, output, cfg):
    match action_type:
        case "note":
            action_note(cfg, data)
        case "wiki":
            action_wiki(cfg, data)
        case "opencode":
            action_opencode(cfg, data)
        case "prompt":
            action_save(cfg, "prompts", data, output)
        case "tz":
            action_save(cfg, "tz", data, output)
        case "remind":
            action_remind(cfg, data)
        case "reply":
            print(f"\n{output}\n")


# ── Main ──────────────────────────────────────────────

def main():
    cfg = load_config()
    llm_cfg = cfg.get("llm", {})
    provider_name = llm_cfg.get("provider", "ollama")
    model = llm_cfg.get("model", "qwen2.5:7b")
    system = read_system_prompt()

    print(f"Ассистент ({provider_name}/{model}). /q выход.\n")

    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/q":
            break

        try:
            if provider_name == "openrouter":
                api_key = os.environ.get("OPENROUTER_API_KEY", "") or llm_cfg.get("api_key", "")
                if not api_key:
                    print("Ошибка: укажи OPENROUTER_API_KEY в переменных окружения")
                    continue
                response = openrouter_generate(api_key, model, system, text)
            elif provider_name == "deepseek":
                api_key = os.environ.get("DEEPSEEK_API_KEY", "") or llm_cfg.get("api_key", "")
                if not api_key:
                    print("Ошибка: укажи DEEPSEEK_API_KEY в переменных окружения")
                    continue
                response = deepseek_generate(api_key, model, system, text)
            else:
                host = cfg.get("ollama_host", "http://localhost:11434")
                response = ollama_generate(host, model, system, text)
        except Exception as e:
            print(f"Ошибка LLM: {e}")
            continue

        action_type, data, output = parse_action(response)
        try:
            execute_action(action_type, data, output, cfg)
        except Exception as e:
            print(f"Ошибка действия: {e}")


if __name__ == "__main__":
    main()
