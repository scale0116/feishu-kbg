"""飞书开放平台 API 轻封装：token 管理 + 常用接口。"""
import time
import requests

API = "https://open.feishu.cn/open-apis"


def _lock_fh(f, lock: bool):
    """跨平台文件锁：Windows用msvcrt，Linux用fcntl。"""
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if lock else msvcrt.LK_UNLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if lock else fcntl.LOCK_UN)


class Feishu:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._expire_at = 0
        # 用户身份token（由 auth_user.py 写入 state.yaml）
        self.state_path = None
        self._user_token = None

    def load_user_token(self, state_path: str) -> str:
        """从 state.yaml 读用户token，过期则用 refresh_token 续期（带文件锁防并发互踢）。"""
        import yaml
        self.state_path = state_path
        lock = open(state_path + ".lock", "a+")
        _lock_fh(lock, True)
        try:
            state = yaml.safe_load(open(state_path, encoding="utf-8")).get("user", {})
            if time.time() < state.get("expire_at", 0) - 120:
                self._user_token = state["access_token"]
                return self._user_token
            r = requests.post(
                "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
                json={"grant_type": "refresh_token", "client_id": self.app_id,
                      "client_secret": self.app_secret,
                      "refresh_token": state["refresh_token"]},
                timeout=15,
            ).json()
            if r.get("code") not in (0, None):
                raise RuntimeError(f"刷新user token失败: {r}")
            state["access_token"] = r["access_token"]
            state["refresh_token"] = r.get("refresh_token", state["refresh_token"])
            state["expire_at"] = time.time() + int(r.get("expires_in", 6900))
            full = yaml.safe_load(open(self.state_path, encoding="utf-8"))
            full["user"] = state
            yaml.safe_dump(full, open(self.state_path, "w", encoding="utf-8"),
                           allow_unicode=True, sort_keys=False)
            self._user_token = state["access_token"]
            return self._user_token
        finally:
            _lock_fh(lock, False)
            lock.close()

    def ureq(self, method: str, path: str, **kw) -> dict:
        """以用户身份调用 API；token 失效时自动强制刷新并重试一次。"""
        for attempt in (1, 2):
            headers = kw.pop("headers", {})
            headers["Authorization"] = f"Bearer {self._user_token}"
            r = requests.request(method, f"{API}{path}", headers=headers, timeout=30, **kw)
            data = r.json()
            if data.get("code") == 0:
                return data.get("data", {})
            if data.get("code") in (99991663, 99991668) and attempt == 1 and self.state_path:
                self._force_refresh()
                continue
            raise RuntimeError(f"{method} {path} 失败: {data}")
        raise RuntimeError(f"{method} {path} 重试后仍失败")

    def _force_refresh(self):
        """无视本地过期时间，强制用 refresh_token 换新 token。"""
        import yaml
        state = yaml.safe_load(open(self.state_path, encoding="utf-8")).get("user", {})
        r = requests.post(
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json={"grant_type": "refresh_token", "client_id": self.app_id,
                  "client_secret": self.app_secret,
                  "refresh_token": state["refresh_token"]},
            timeout=15,
        ).json()
        if r.get("code") not in (0, None):
            raise RuntimeError(f"刷新user token失败: {r}")
        state["access_token"] = r["access_token"]
        state["refresh_token"] = r.get("refresh_token", state["refresh_token"])
        state["expire_at"] = time.time() + int(r.get("expires_in", 6900))
        full = yaml.safe_load(open(self.state_path, encoding="utf-8"))
        full["user"] = state
        yaml.safe_dump(full, open(self.state_path, "w", encoding="utf-8"),
                       allow_unicode=True, sort_keys=False)
        self._user_token = state["access_token"]

    def token(self) -> str:
        if self._token and time.time() < self._expire_at - 120:
            return self._token
        r = requests.post(
            f"{API}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        ).json()
        if r.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {r}")
        self._token = r["tenant_access_token"]
        self._expire_at = time.time() + r.get("expire", 3600)
        return self._token

    def req(self, method: str, path: str, **kw) -> dict:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token()}"
        r = requests.request(method, f"{API}{path}", headers=headers, timeout=30, **kw)
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"{method} {path} 失败: {data}")
        return data.get("data", {})

    # ---- 知识库 ----
    def list_wiki_spaces(self) -> list:
        return self.req("GET", "/wiki/v2/spaces", params={"page_size": 50}).get("items", [])

    def create_wiki_space(self, name: str) -> dict:
        return self.req("POST", "/wiki/v2/spaces", json={"name": name})

    def list_wiki_nodes(self, space_id: str) -> list:
        items, token = [], ""
        while True:
            d = self.req("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                         params={"page_size": 50, "page_token": token})
            items += d.get("items", [])
            token = d.get("page_token", "")
            if not d.get("has_more"):
                return items

    def create_wiki_node(self, space_id: str, title: str) -> dict:
        return self.req("POST", f"/wiki/v2/spaces/{space_id}/nodes",
                        json={"obj_type": "docx", "obj_id": "", "title": title,
                              "node_type": "origin"})

    # ---- 文档 ----
    def create_doc_in_folder(self, folder_token: str, title: str) -> dict:
        return self.req("POST", "/docx/v1/documents",
                        json={"folder_token": folder_token, "title": title})

    def doc_raw_content(self, document_id: str) -> str:
        return self.req("GET", f"/docx/v1/documents/{document_id}/raw_content").get("content", "")

    def append_doc_blocks(self, document_id: str, blocks: list) -> dict:
        return self.req("POST", f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                        json={"children": blocks})

    # ---- 消息（IM）----
    def list_chats(self) -> list:
        items, token = [], ""
        while True:
            d = self.req("GET", "/im/v1/chats", params={"page_size": 100, "page_token": token})
            items += d.get("items", [])
            token = d.get("page_token", "")
            if not d.get("has_more"):
                return items

    def list_messages(self, container_id: str, page_size: int = 20) -> list:
        """按时间倒序拉取最近消息（新→旧），时间过滤由调用方用游标完成。"""
        d = self.req("GET", "/im/v1/messages",
                     params={"container_id_type": "chat", "container_id": container_id,
                             "sort_type": "ByCreateTimeDesc", "page_size": page_size})
        return d.get("items", [])

    def send_text(self, chat_id: str, text: str) -> dict:
        return self.req("POST", "/im/v1/messages?receive_id_type=chat_id",
                        json={"receive_id": chat_id, "msg_type": "text",
                              "content": f'{{"text":"{text}"}}'})
