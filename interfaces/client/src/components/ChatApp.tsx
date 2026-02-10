// @ts-nocheck
import React, { useCallback, useEffect, useRef, useState } from "react";
import { marked } from "marked";
import { MoreVertical, Pencil, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";

marked.setOptions({ breaks: true });

function parseChatId() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("chat_id");
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function setChatParam(chatId) {
  const url = new URL(window.location.href);
  url.searchParams.set("chat_id", String(chatId));
  window.history.pushState({}, "", url);
}

function parseDebugMessageId() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("debug_message_id");
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function setDebugParam(messageId) {
  const url = new URL(window.location.href);
  url.searchParams.set("debug_message_id", String(messageId));
  window.history.pushState({}, "", url);
}

function clearDebugParam() {
  const url = new URL(window.location.href);
  url.searchParams.delete("debug_message_id");
  window.history.pushState({}, "", url);
}

function MenuIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      strokeLinejoin="round"
      strokeLinecap="round"
      strokeWidth="2"
      fill="none"
      stroke="currentColor"
      className="my-1.5 inline-block size-4"
    >
      <path d="M4 4m0 2a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2z" />
      <path d="M9 4v16" />
      <path d="M14 10l2 2l-2 2" />
    </svg>
  );
}

function PlusIcon({ className }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      strokeLinejoin="round"
      strokeLinecap="round"
      strokeWidth="2"
      fill="none"
      stroke="currentColor"
      className={className}
    >
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}


function domainFromUrl(url) {
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

export function ChatApp() {
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(parseChatId());
  const [debugMessageId, setDebugMessageId] = useState(parseDebugMessageId());
  const [debugState, setDebugState] = useState({
    loading: false,
    error: "",
    data: null,
  });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [profile, setProfile] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [profileOverride, setProfileOverride] = useState(
    () => window.localStorage.getItem("profileIp") || ""
  );
  const [deleteTarget, setDeleteTarget] = useState(null);
  const listRef = useRef(null);
  const searchRef = useRef(null);
  const inputRef = useRef(null);
  const profileDropdownRef = useRef(null);

  const apiFetch = useCallback(
    (path, options = {}) => {
      const headers = new Headers(options.headers || {});
      if (profileOverride) {
        headers.set("X-Profile-IP", profileOverride);
      }
      return fetch(`${API_BASE}${path}`, { ...options, headers });
    },
    [profileOverride]
  );

  const scrollToBottom = (force = false) => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (force || nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  };

  const loadChats = async () => {
    const res = await apiFetch("/chats");
    const data = await res.json();
    const items = data.chats || [];
    setChats(items);
    if (!activeChatId && items.length) {
      setActiveChatId(items[0].id);
    }
  };

  const loadMessages = async (chatId) => {
    if (!chatId) return;
    const res = await apiFetch(`/chats/${chatId}/messages?limit=50`);
    const data = await res.json();
    setMessages(data.messages || []);
    scrollToBottom(true);
  };

  const createChat = useCallback(async () => {
    const res = await apiFetch("/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      return null;
    }
    const data = await res.json();
    const chat = data.chat;
    if (!chat || !chat.id) {
      return null;
    }
    setChats((prev) => [chat, ...prev.filter((c) => c.id !== chat.id)]);
    setActiveChatId(chat.id);
    setMessages([]);
    setChatParam(chat.id);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
    return chat.id;
  }, [apiFetch]);

  const sendFeedback = async (messageId, rating) => {
    await apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, rating }),
    });
  };

  const openDebugPage = (messageId) => {
    if (!messageId) return;
    setDebugMessageId(messageId);
    setDebugParam(messageId);
  };

  const closeDebugPage = () => {
    setDebugMessageId(null);
    setDebugState({ loading: false, error: "", data: null });
    clearDebugParam();
  };

  const renameChat = async (chat) => {
    const current = chat.title || `Chat ${chat.id}`;
    const nextTitle = window.prompt("Rename chat", current);
    if (nextTitle === null) return;
    if (!nextTitle.trim()) return;
    const res = await apiFetch(`/chats/${chat.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: nextTitle }),
    });
    if (res.ok) {
      loadChats();
    }
  };

  const deleteChat = async (chat) => {
    if (!chat) return;
    const res = await apiFetch(`/chats/${chat.id}`, { method: "DELETE" });
    if (!res.ok) return;
    setChats((prev) => prev.filter((c) => c.id !== chat.id));
    if (activeChatId === chat.id) {
      const remaining = chats.filter((c) => c.id !== chat.id);
      if (remaining.length) {
        setActiveChatId(remaining[0].id);
      } else {
        createChat();
      }
    }
  };

  const requestDelete = (chat) => {
    setDeleteTarget(chat);
  };

  const confirmDelete = async () => {
    const chat = deleteTarget;
    setDeleteTarget(null);
    await deleteChat(chat);
  };

  const streamAnswer = async (message, streamId, assistantId, chatId) => {
    const body = new URLSearchParams({
      message,
      stream_id: streamId,
      message_id: String(assistantId),
      chat_id: String(chatId),
    });
    const response = await apiFetch("/stream", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (!response.ok || !response.body) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, content: "Error streaming response." } : m
        )
      );
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullText = "";
    let sources = [];

    const updateAssistant = (fields) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, ...fields } : m))
      );
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";

      for (const chunk of parts) {
        let eventType = "message";
        let dataStr = "";
        chunk.split("\n").forEach((line) => {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataStr += line.slice(5).trim();
          }
        });
        if (!dataStr) continue;
        let payload;
        try {
          payload = JSON.parse(dataStr);
        } catch {
          continue;
        }
        if (eventType === "delta") {
          const delta = payload.text || "";
          if (delta) {
            fullText += delta;
            updateAssistant({ content: fullText });
            scrollToBottom();
          }
        } else if (eventType === "sources") {
          sources = payload.sources || [];
          updateAssistant({ sources });
        } else if (eventType === "debug") {
          updateAssistant({ has_debug: true });
        } else if (eventType === "error") {
          updateAssistant({ content: payload.error || "Error" });
        } else if (eventType === "done") {
          updateAssistant({ content: fullText, sources });
          scrollToBottom(true);
        }
      }
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    const message = input.trim();
    if (!message) return;

    setInput("");
    setSending(true);

    try {
      let chatId = activeChatId;
      if (!chatId) {
        chatId = await createChat();
      }
      if (!chatId) {
        throw new Error("Unable to create chat");
      }

      const res = await apiFetch(`/chats/${chatId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (!res.ok) {
        throw new Error("Failed to send message");
      }
      const data = await res.json();
      const userMsg = {
        id: data.user_message_id,
        role: "user",
        content: message,
        sources: [],
      };
      const assistantMsg = {
        id: data.assistant_message_id,
        role: "assistant",
        content: "",
        sources: [],
        has_debug: false,
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      scrollToBottom(true);
      await streamAnswer(
        message,
        data.stream_id,
        data.assistant_message_id,
        chatId
      );
      loadChats();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now(),
          role: "assistant",
          content: "Error sending message.",
          sources: [],
          has_debug: false,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    loadChats();
  }, [apiFetch]);

  useEffect(() => {
    const loadProfile = async () => {
      const res = await apiFetch("/profile");
      const data = await res.json();
      setProfile(data);
    };
    const loadProfiles = async () => {
      const res = await apiFetch("/profiles");
      const data = await res.json();
      setProfiles(data.profiles || []);
    };
    loadProfile();
    loadProfiles();
  }, [apiFetch]);

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    setDrawerOpen(mq.matches);
  }, []);

  useEffect(() => {
    if (activeChatId) {
      setChatParam(activeChatId);
      loadMessages(activeChatId);
    }
  }, [activeChatId]);

  useEffect(() => {
    const onPopState = () => {
      setActiveChatId(parseChatId());
      setDebugMessageId(parseDebugMessageId());
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!debugMessageId) return;
    let cancelled = false;
    const loadDebug = async () => {
      setDebugState({ loading: true, error: "", data: null });
      try {
        const res = await apiFetch(`/messages/${debugMessageId}/debug`);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || "Failed to load debug details");
        }
        const data = await res.json();
        if (!cancelled) {
          setDebugState({ loading: false, error: "", data });
        }
      } catch (err) {
        if (!cancelled) {
          setDebugState({
            loading: false,
            error: err?.message || "Failed to load debug details",
            data: null,
          });
        }
      }
    };
    loadDebug();
    return () => {
      cancelled = true;
    };
  }, [debugMessageId, apiFetch]);

  useEffect(() => {
    scrollToBottom(false);
  }, [messages]);

  const selectProfile = (ip) => {
    const next = ip || "";
    if (next) {
      window.localStorage.setItem("profileIp", next);
    } else {
      window.localStorage.removeItem("profileIp");
    }
    setProfileOverride(next);
    setActiveChatId(null);
    setMessages([]);
  };

  const addProfile = async () => {
    const ip = window.prompt("Enter IP address");
    if (!ip) return;
    const res = await apiFetch("/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip }),
    });
    if (res.ok) {
      const data = await apiFetch("/profiles");
      const list = await data.json();
      setProfiles(list.profiles || []);
      selectProfile(ip);
    }
  };

  const closeProfileDropdown = () => {
    if (profileDropdownRef.current) {
      profileDropdownRef.current.blur();
    }
  };

  useEffect(() => {
    const handler = (event) => {
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && key === "o") {
        event.preventDefault();
        createChat();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && key === "k") {
        event.preventDefault();
        setDrawerOpen(true);
        requestAnimationFrame(() => {
          searchRef.current?.focus();
        });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [createChat]);

  const normalizedQuery = searchQuery.trim().toLowerCase();
  const visibleChats = normalizedQuery
    ? chats.filter((chat) =>
        (chat.title || `Chat ${chat.id}`)
          .toLowerCase()
          .includes(normalizedQuery)
      )
    : chats;

  if (debugMessageId) {
    const debugPayload = debugState.data?.debug || {};
    const retrieval = debugPayload.retrieval || {};
    const rankedChunks = retrieval.ranked_chunks || [];
    const sources = debugPayload.sources || [];
    const llmRequest = debugPayload.llm_request || {};
    const responseText = debugPayload.llm_response_text || "";

    return (
      <div className="min-h-screen bg-base-200 p-4 md:p-6">
        <div className="max-w-6xl mx-auto flex flex-col gap-4">
          <div className="card bg-base-100 border border-base-300 shadow-sm">
            <div className="card-body">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h1 className="text-xl font-semibold">Query Debug Details</h1>
                  <div className="text-sm opacity-70">
                    message_id: {debugMessageId}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <a
                    className="btn btn-ghost btn-sm"
                    href={`${API_BASE}/debug/hybrid-retrieval-doc`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    How scoring works
                  </a>
                  <button className="btn btn-outline btn-sm" type="button" onClick={closeDebugPage}>
                    Back to chat
                  </button>
                </div>
              </div>
            </div>
          </div>

          {debugState.loading && (
            <div className="alert">
              <span>Loading debug details...</span>
            </div>
          )}

          {debugState.error && (
            <div className="alert alert-error">
              <span>{debugState.error}</span>
            </div>
          )}

          {!debugState.loading && !debugState.error && (
            <>
              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">Question</h2>
                  <pre className="debug-pre">{debugPayload.query || "-"}</pre>
                </div>
              </div>

              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">Sources</h2>
                  <div className="overflow-x-auto">
                    <table className="table table-sm">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Score</th>
                          <th>Vector</th>
                          <th>BM25</th>
                          <th>v_raw</th>
                          <th>b_raw</th>
                          <th>Extract</th>
                          <th>URL</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sources.map((source, idx) => (
                          <tr key={`${source.extract_id || idx}-${idx}`}>
                            <td>{idx + 1}</td>
                            <td>{Number(source.score || 0).toFixed(4)}</td>
                            <td>{source.from_vector ? "yes" : "no"}</td>
                            <td>{source.from_bm25 ? "yes" : "no"}</td>
                            <td>{Number(source.vector_score_raw || 0).toFixed(4)}</td>
                            <td>{Number(source.bm25_score_raw || 0).toFixed(4)}</td>
                            <td>{source.extract_id}</td>
                            <td className="break-all">
                              {source.url ? (
                                <a href={source.url} target="_blank" rel="noreferrer" className="link link-primary">
                                  {source.url}
                                </a>
                              ) : (
                                "-"
                              )}
                            </td>
                          </tr>
                        ))}
                        {sources.length === 0 && (
                          <tr>
                            <td colSpan={8} className="opacity-70">
                              No sources available.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">Hybrid Ranking</h2>
                  <div className="text-sm opacity-80 mb-2">
                    vector candidates: {retrieval.candidate_counts?.vector ?? "-"} | bm25 candidates:{" "}
                    {retrieval.candidate_counts?.bm25 ?? "-"} | merged:{" "}
                    {retrieval.candidate_counts?.merged ?? "-"}
                  </div>
                  <div className="overflow-x-auto">
                    <table className="table table-sm">
                      <thead>
                        <tr>
                          <th>Rank</th>
                          <th>Fusion</th>
                          <th>Vector</th>
                          <th>BM25</th>
                          <th>v_norm</th>
                          <th>b_norm</th>
                          <th>Chunk</th>
                          <th>URL</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rankedChunks.map((item) => (
                          <tr key={item.chunk_id}>
                            <td>{item.rank}</td>
                            <td>{Number(item.score || 0).toFixed(4)}</td>
                            <td>{item.from_vector ? "yes" : "no"}</td>
                            <td>{item.from_bm25 ? "yes" : "no"}</td>
                            <td>{Number(item.vector_score_norm || 0).toFixed(4)}</td>
                            <td>{Number(item.bm25_score_norm || 0).toFixed(4)}</td>
                            <td>{item.chunk_id}</td>
                            <td className="break-all">
                              {item.url ? (
                                <a href={item.url} target="_blank" rel="noreferrer" className="link link-primary">
                                  {item.url}
                                </a>
                              ) : (
                                "-"
                              )}
                            </td>
                          </tr>
                        ))}
                        {rankedChunks.length === 0 && (
                          <tr>
                            <td colSpan={8} className="opacity-70">
                              No ranking data available.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">LLM Request Payload</h2>
                  <h3 className="font-semibold text-sm">System</h3>
                  <pre className="debug-pre">{llmRequest.system_text || "-"}</pre>
                  <h3 className="font-semibold text-sm mt-3">User (full question + context)</h3>
                  <pre className="debug-pre">{llmRequest.user_text || "-"}</pre>
                </div>
              </div>

              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">LLM Response</h2>
                  <pre className="debug-pre">{responseText || "-"}</pre>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`drawer min-h-screen bg-base-200 ${drawerOpen ? "drawer-open" : ""}`}
    >
      <input
        id="nav-drawer"
        type="checkbox"
        className="drawer-toggle"
        checked={drawerOpen}
        onChange={(event) => setDrawerOpen(event.target.checked)}
      />

      <div className="drawer-content flex flex-col">
        <nav className="navbar w-full bg-base-300">
          <label
            htmlFor="nav-drawer"
            aria-label="open sidebar"
            className="btn btn-square btn-ghost"
          >
            <MenuIcon />
          </label>
          <div className="px-4 text-xl font-semibold">Chat</div>
          <div className="ml-auto">
            {profile && profile.avatar ? (
              <div className="dropdown dropdown-end">
                <button
                  type="button"
                  className="btn btn-ghost btn-circle"
                  tabIndex={0}
                  ref={profileDropdownRef}
                >
                  <div className="avatar placeholder">
                    <div
                      className="w-9 rounded-full text-white flex items-center justify-center leading-none"
                      style={{ backgroundColor: profile.avatar.color }}
                    >
                      <span className="text-sm font-semibold">
                        {profile.avatar.initials}
                      </span>
                    </div>
                  </div>
                </button>
                <ul className="dropdown-content menu p-2 shadow bg-base-100 rounded-box w-56 z-30">
                  <li className="menu-title">
                    <span>Profiles</span>
                  </li>
                  <li>
                    <button
                      type="button"
                      onClick={() => {
                        selectProfile("");
                        closeProfileDropdown();
                      }}
                    >
                      Use my IP ({profile.ip})
                    </button>
                  </li>
                  {profiles.map((item) => (
                    <li key={item.ip}>
                      <button
                        type="button"
                        onClick={() => {
                          selectProfile(item.ip);
                          closeProfileDropdown();
                        }}
                      >
                        <span
                          className="inline-block w-3 h-3 rounded-full mr-2"
                          style={{ backgroundColor: item.avatar.color }}
                        />
                        {item.ip}
                      </button>
                    </li>
                  ))}
                  <li>
                    <button
                      type="button"
                      onClick={async () => {
                        await addProfile();
                        closeProfileDropdown();
                      }}
                    >
                      Add profile...
                    </button>
                  </li>
                </ul>
              </div>
            ) : null}
          </div>
        </nav>

        <div className="p-4">
          <div className="card bg-base-100 shadow-xl border border-base-300">
            <div className="card-body flex flex-col gap-4">
              <div
                ref={listRef}
                className="flex-1 overflow-y-auto space-y-4 p-4 bg-base-200 rounded-box min-h-[60vh]"
              >
                {messages.length === 0 ? (
                  <div className="prose max-w-none">Hi! Ask me anything about your data.</div>
                ) : (
                  messages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`chat ${msg.role === "user" ? "chat-end" : "chat-start"} w-full`}
                    >
                      {msg.role === "user" ? (
                        <div className="chat-bubble chat-bubble-primary whitespace-pre-wrap">
                          {msg.content}
                        </div>
                      ) : (
                        <div className="w-full">
                          <div
                            className="markdown"
                            dangerouslySetInnerHTML={{
                              __html: marked.parse(msg.content || ""),
                            }}
                          />
                          {msg.sources && msg.sources.length > 0 && (
                            <div className="flex gap-2 mt-2">
                              {msg.sources.map((source, idx) => {
                                const domain = domainFromUrl(source.url || "");
                                if (!domain) return null;
                                return (
                                  <a
                                    key={`${domain}-${idx}`}
                                    href={source.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="avatar"
                                  >
                                    <img
                                      src={`https://www.google.com/s2/favicons?domain=${domain}&sz=64`}
                                      alt={domain}
                                      className="mask mask-squircle w-6 h-6"
                                    />
                                  </a>
                                );
                              })}
                            </div>
                          )}
                          <div className="flex gap-2 mt-2">
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => openDebugPage(msg.id)}
                              disabled={!msg.has_debug}
                              title={msg.has_debug ? "Open debug details" : "Debug not available yet"}
                              type="button"
                            >
                              Debug
                            </button>
                            <button
                              className="btn btn-square btn-ghost"
                              onClick={() => sendFeedback(msg.id, 1)}
                              type="button"
                            >
                              <ThumbsUp className="size-[1.2em]" />
                            </button>
                            <button
                              className="btn btn-square btn-ghost"
                              onClick={() => sendFeedback(msg.id, -1)}
                              type="button"
                            >
                              <ThumbsDown className="size-[1.2em]" />
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>

              <form className="flex gap-3" onSubmit={handleSubmit}>
                <input
                  ref={inputRef}
                  className="input input-bordered w-full"
                  placeholder="Type your message..."
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  disabled={sending}
                />
                <button className="btn btn-primary" type="submit" disabled={sending}>
                  Send
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>

      <div className="drawer-side is-drawer-close:overflow-visible">
        <label htmlFor="nav-drawer" aria-label="close sidebar" className="drawer-overlay"></label>
        <div className="flex min-h-full flex-col items-start bg-base-200 is-drawer-close:w-14 is-drawer-open:w-96">
          <div className="p-2 w-full">
            <div className="text-lg font-semibold is-drawer-open">Chats</div>
            <button
              className="btn btn-primary btn-sm w-full gap-3 mt-2 items-center"
              onClick={createChat}
              type="button"
            >
              <PlusIcon className="is-drawer-close size-4" />
              <span className="is-drawer-open flex items-center gap-2">
                <span>New chat (Ctrl+Shift+O)</span>
              </span>
            </button>
            <div className="is-drawer-open mt-2">
              <input
                ref={searchRef}
                className="input input-bordered input-sm w-full"
                placeholder="Search chats (Ctrl+K)"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
            </div>
          </div>

          <ul className="menu w-full grow">
            {visibleChats.map((chat) => {
              const title = chat.title || `Chat ${chat.id}`;
              const active = chat.id === activeChatId;
              return (
                <li key={chat.id} className="group">
                  <div className="flex items-center justify-between w-full gap-2">
                    <button
                      type="button"
                      className={`flex-1 min-w-0 justify-start gap-3 ${active ? "active font-semibold" : ""} is-drawer-close:tooltip is-drawer-close:tooltip-right`}
                      data-tip={title}
                      onClick={() => setActiveChatId(chat.id)}
                    >
                      <span className="is-drawer-open truncate">{title}</span>
                    </button>
                    <div className="dropdown dropdown-end is-drawer-open relative z-20">
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs opacity-0 group-hover:opacity-100 is-drawer-close:opacity-100"
                        tabIndex={0}
                      >
                        <MoreVertical className="size-4" />
                      </button>
                      <ul
                        tabIndex={0}
                        className="dropdown-content menu p-2 shadow bg-base-100 rounded-box w-40 z-30"
                      >
                        <li>
                          <button type="button" onClick={() => renameChat(chat)}>
                            <Pencil className="size-4" />
                            Rename
                          </button>
                        </li>
                        <li>
                          <button type="button" onClick={() => requestDelete(chat)}>
                            <Trash2 className="size-4" />
                            Delete
                          </button>
                        </li>
                      </ul>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      {deleteTarget && (
        <dialog className="modal modal-open">
          <div className="modal-box">
            <h3 className="font-semibold text-lg">Delete chat?</h3>
            <p className="py-3">
              This will permanently delete "{deleteTarget.title || `Chat ${deleteTarget.id}`}"
              and its messages.
            </p>
            <div className="modal-action">
              <button className="btn" type="button" onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button className="btn btn-error" type="button" onClick={confirmDelete}>
                Delete
              </button>
            </div>
          </div>
          <form method="dialog" className="modal-backdrop">
            <button onClick={() => setDeleteTarget(null)}>close</button>
          </form>
        </dialog>
      )}
    </div>
  );
}

export default ChatApp;

