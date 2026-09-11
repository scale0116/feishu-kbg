"""OpenAI 兼容对话客户端（智谱/DeepSeek/豆包均可通过 config 切换）。"""
import requests


def chat(cfg: dict, system: str, user: str, temperature: float = 0.3) -> str:
    c = cfg["llm"]
    r = requests.post(
        f"{c['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {c['api_key']}"},
        json={"model": c["model"], "temperature": temperature,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
