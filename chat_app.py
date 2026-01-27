from fasthtml.common import *
import importlib.util
from pathlib import Path
from urllib.parse import urlparse

LLM_PATH = Path(__file__).with_name("05_llmapi.py")
_spec = importlib.util.spec_from_file_location("llmapi", LLM_PATH)
_llmapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llmapi)

app, rt = fast_app()


def chat_bubble(role, text, sources=None):
    children = [Div(role, cls="msg-role"), Div("", cls="msg-text", data_md=text)]
    if sources:
        icons = []
        for source in sources:
            url = source.get("url")
            if not url:
                continue
            domain = urlparse(url).netloc
            if not domain:
                continue
            icons.append(
                A(
                    Img(
                        src=f"https://www.google.com/s2/favicons?domain={domain}&sz=64",
                        alt=domain,
                        title=domain,
                    ),
                    href=url,
                    target="_blank",
                    rel="noreferrer",
                )
            )
        if icons:
            children.append(Div(*icons, cls="msg-sources"))
    return Div(*children, cls=f"msg msg-{role}")


@rt("/")
def get():
    return Html(
        Head(
            Title("Chat"),
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(
                rel="preconnect",
                href="https://fonts.googleapis.com",
            ),
            Link(
                rel="preconnect",
                href="https://fonts.gstatic.com",
                crossorigin="",
            ),
            Link(
                rel="stylesheet",
                href=(
                    "https://fonts.googleapis.com/css2?"
                    "family=Space+Grotesk:wght@400;500;600;700&"
                    "family=Fraunces:opsz,wght@9..144,600&display=swap"
                ),
            ),
            Script(src="https://unpkg.com/htmx.org@1.9.12"),
            Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"),
            Style(
                """
                :root {
                  --bg-1: #f7f4ee;
                  --bg-2: #f0efe9;
                  --ink: #1b1b1f;
                  --ink-muted: #6c6a66;
                  --accent: #145c62;
                  --accent-2: #c47f2c;
                  --card: #fbfbf8;
                  --shadow: 0 22px 60px rgba(27, 27, 31, 0.18);
                  --radius: 22px;
                }

                * { box-sizing: border-box; }

                body {
                  margin: 0;
                  font-family: "Space Grotesk", sans-serif;
                  color: var(--ink);
                  background:
                    radial-gradient(1200px 600px at 10% -10%, #dfe9e6, transparent 60%),
                    radial-gradient(900px 500px at 110% 10%, #f4dbc1, transparent 60%),
                    linear-gradient(160deg, var(--bg-1), var(--bg-2));
                  min-height: 100vh;
                  display: flex;
                  align-items: center;
                  justify-content: center;
                  padding: 32px 18px 48px;
                }

                .shell {
                  width: min(980px, 100%);
                  display: grid;
                  gap: 18px;
                }

                .header {
                  display: flex;
                  align-items: center;
                  justify-content: space-between;
                  gap: 20px;
                  padding: 14px 6px 6px;
                }

                .brand {
                  font-family: "Fraunces", serif;
                  font-size: clamp(24px, 3vw, 30px);
                  letter-spacing: 0.02em;
                }

                .status {
                  font-size: 13px;
                  color: var(--ink-muted);
                  background: rgba(20, 92, 98, 0.12);
                  border: 1px solid rgba(20, 92, 98, 0.28);
                  padding: 6px 12px;
                  border-radius: 999px;
                }

                .chat {
                  background: var(--card);
                  border-radius: var(--radius);
                  box-shadow: var(--shadow);
                  border: 1px solid rgba(27, 27, 31, 0.08);
                  display: flex;
                  flex-direction: column;
                  height: min(70vh, 720px);
                  overflow: hidden;
                }

                .messages {
                  flex: 1;
                  overflow-y: auto;
                  padding: 28px clamp(18px, 3vw, 32px);
                  display: flex;
                  flex-direction: column;
                  gap: 18px;
                  background:
                    linear-gradient(180deg, rgba(250, 250, 247, 0.9), rgba(250, 250, 247, 0.6));
                }

                .msg {
                  display: grid;
                  gap: 6px;
                  max-width: 78%;
                  animation: float-in 280ms ease-out both;
                }

                .msg-role {
                  font-size: 12px;
                  text-transform: uppercase;
                  letter-spacing: 0.2em;
                  color: var(--ink-muted);
                }

                .msg-text {
                  padding: 14px 18px;
                  border-radius: 18px;
                  line-height: 1.5;
                  font-size: 15.5px;
                }

                .msg-text p { margin: 0 0 10px; }
                .msg-text p:last-child { margin-bottom: 0; }
                .msg-text ul, .msg-text ol { margin: 0 0 10px 20px; padding: 0; }
                .msg-text li { margin: 0 0 6px; }
                .msg-text a { color: inherit; text-decoration: underline; }
                .msg-text code {
                  font-family: "Space Grotesk", monospace;
                  font-size: 0.95em;
                  background: rgba(20, 92, 98, 0.08);
                  padding: 0 6px;
                  border-radius: 6px;
                }
                .msg-text pre {
                  background: rgba(20, 92, 98, 0.08);
                  padding: 12px 14px;
                  border-radius: 12px;
                  overflow: auto;
                  margin: 0 0 10px;
                }

                .msg-sources {
                  display: flex;
                  gap: 8px;
                  margin-top: 6px;
                  align-items: center;
                }

                .msg-sources img {
                  width: 20px;
                  height: 20px;
                  border-radius: 6px;
                  box-shadow: 0 4px 10px rgba(27, 27, 31, 0.2);
                  background: #fff;
                }

                .msg-user {
                  align-self: flex-end;
                }

                .msg-user .msg-text {
                  background: var(--accent);
                  color: #fff;
                  border-top-right-radius: 6px;
                }

                .msg-assistant .msg-text {
                  background: #f0eee9;
                  border: 1px solid rgba(27, 27, 31, 0.08);
                  border-top-left-radius: 6px;
                }

                .composer {
                  display: grid;
                  grid-template-columns: 1fr auto;
                  gap: 12px;
                  padding: 18px 22px 22px;
                  border-top: 1px solid rgba(27, 27, 31, 0.08);
                  background: #f9f8f4;
                }

                .composer input {
                  border: 1px solid rgba(27, 27, 31, 0.16);
                  border-radius: 16px;
                  padding: 14px 16px;
                  font-size: 15px;
                  outline: none;
                  background: #fff;
                }

                .composer input:focus {
                  border-color: var(--accent);
                  box-shadow: 0 0 0 3px rgba(20, 92, 98, 0.18);
                }

                .composer button {
                  border: none;
                  border-radius: 14px;
                  padding: 12px 18px;
                  background: linear-gradient(135deg, var(--accent), #1a737d);
                  color: #fff;
                  font-weight: 600;
                  cursor: pointer;
                  transition: transform 150ms ease, box-shadow 150ms ease;
                }

                .composer button:hover {
                  transform: translateY(-1px);
                  box-shadow: 0 8px 20px rgba(20, 92, 98, 0.22);
                }

                @keyframes float-in {
                  from { opacity: 0; transform: translateY(10px) scale(0.98); }
                  to { opacity: 1; transform: translateY(0) scale(1); }
                }

                @media (max-width: 680px) {
                  .chat { height: min(74vh, 640px); }
                  .msg { max-width: 92%; }
                }
                """
            ),
        ),
        Body(
            Div(
                Div(
                    Div("Co-Pilot Chat", cls="brand"),
                    Div("Prototype", cls="status"),
                    cls="header",
                ),
                Div(
                    Div(
                        chat_bubble("assistant", "Hi! Ask me anything about your data."),
                        id="messages",
                        cls="messages",
                    ),
                    Form(
                        Input(
                            id="message",
                            name="message",
                            placeholder="Type your message...",
                            autocomplete="off",
                            required=True,
                        ),
                        Button("Send", type="submit"),
                        cls="composer",
                        hx_post="/message",
                        hx_target="#messages",
                        hx_swap="beforeend",
                    ),
                    cls="chat",
                ),
                cls="shell",
            ),
            Script(
                """
                function renderMarkdown(scope) {
                  const blocks = (scope || document).querySelectorAll(".msg-text[data-md]");
                  blocks.forEach((block) => {
                    if (block.dataset.rendered === "1") return;
                    const md = block.dataset.md || "";
                    block.innerHTML = marked.parse(md);
                    block.dataset.rendered = "1";
                  });
                }

                document.body.addEventListener("htmx:afterRequest", () => {
                  const input = document.querySelector("#message");
                  if (input) input.value = "";
                  const list = document.querySelector("#messages");
                  if (list && list.lastElementChild) {
                    list.lastElementChild.scrollIntoView({ behavior: "smooth", block: "end" });
                  }
                  renderMarkdown(list || document);
                });
                window.addEventListener("load", () => {
                  const list = document.querySelector("#messages");
                  if (list && list.lastElementChild) {
                    list.lastElementChild.scrollIntoView({ behavior: "instant", block: "end" });
                  }
                  renderMarkdown(document);
                });
                """
            ),
        ),
    )


@rt("/message", methods=["POST"])
def post(message: str):
    user_msg = chat_bubble("user", message)
    try:
        reply, sources = _llmapi.answer_query_with_context(
            message, top_k=5, max_extracts=3, verbose=False
        )
    except Exception as exc:
        reply = f"Sorry, I hit an error: {exc}"
        sources = []

    if reply:
        assistant_msg = chat_bubble("assistant", reply, sources=sources)
    else:
        assistant_msg = chat_bubble(
            "assistant",
            "I couldn't find relevant context in the embeddings database.",
        )
    return user_msg, assistant_msg


if __name__ == "__main__":
    serve()
