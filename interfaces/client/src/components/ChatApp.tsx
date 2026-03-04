// @ts-nocheck
import React, { useCallback, useEffect, useRef, useState } from "react";
import { marked } from "marked";
import {
  ChevronDown,
  ChevronRight,
  MoreVertical,
  Pencil,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "/api";
const CHANGELOG_FALLBACK_HREF = "/connections/reference/changelog";
const STREAM_ERROR_FALLBACK_MESSAGE =
  "I hit a temporary problem generating a response. Please try again.";
const CHAT_TIME_ZONE = "America/New_York";
const ISO_TZ_SUFFIX_RE = /(?:[zZ]|[+\-]\d{2}:\d{2})$/;
const INLINE_SOURCE_LINKS_MAX = 2;
const PROCEDURE_LINKS_TRAILING_BLOCK_RE =
  /(?:\r?\n){2}Procedure links:\s*(?:\r?\n)- \[[^\]]+\]\([^)]+\)(?:\r?\n- \[[^\]]+\]\([^)]+\))*\s*$/i;
const TAB_STEP_FRAGMENT_RE = /#tab-step\d+\b/i;
const LEGACY_SOURCE_MIN_V_RAW = 0.65;
const LEGACY_SOURCE_MIN_B_RAW = 10.0;

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
    return new URL(url, window.location.origin).hostname;
  } catch {
    return "";
  }
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function canonicalSourceKey(source) {
  const raw = String(source?.url_canonical || source?.url || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, window.location.origin);
    parsed.hash = "";
    const scheme = (parsed.protocol || "https:").toLowerCase();
    const host = (parsed.host || "").toLowerCase();
    const pathDecoded = safeDecode(parsed.pathname || "/");
    const path = pathDecoded === "/" ? "/" : pathDecoded.replace(/\/+$/, "") || "/";

    const docs = parsed.searchParams.get("docs");
    if (docs) {
      const docsNorm = safeDecode(docs).trim().replace(/^\/+|\/+$/g, "");
      if (docsNorm) {
        return `${scheme}//${host}${path}?docs=${docsNorm.toLowerCase()}`;
      }
    }

    const entries = [];
    for (const [key, value] of parsed.searchParams.entries()) {
      const k = safeDecode(key).trim().toLowerCase();
      const v = safeDecode(value).trim().toLowerCase();
      if (!k) continue;
      entries.push([k, v]);
    }
    entries.sort((a, b) => {
      if (a[0] === b[0]) return a[1].localeCompare(b[1]);
      return a[0].localeCompare(b[0]);
    });
    if (entries.length === 0) {
      return `${scheme}//${host}${path}`;
    }
    const query = entries.map(([k, v]) => `${k}=${v}`).join("&");
    return `${scheme}//${host}${path}?${query}`;
  } catch {
    return raw.toLowerCase();
  }
}

function sourceLabelFromUrl(source) {
  const raw = String(source?.url || source?.url_canonical || "").trim();
  if (!raw) return "source";
  try {
    const parsed = new URL(raw, window.location.origin);
    const docs = parsed.searchParams.get("docs");
    if (docs) {
      const docsPath = safeDecode(docs).trim().replace(/^\/+|\/+$/g, "");
      if (docsPath) {
        const parts = docsPath.split("/").filter(Boolean);
        if (parts.length >= 2) {
          return `${parts[parts.length - 2]} / ${parts[parts.length - 1]}`;
        }
        return parts[0];
      }
    }
    const host = (parsed.hostname || "").replace(/^www\./i, "");
    const path = safeDecode(parsed.pathname || "").replace(/\/+$/, "");
    if (!path || path === "/") {
      return host || raw;
    }
    const segments = path.split("/").filter(Boolean);
    const tail = segments.slice(-2).join(" / ");
    return host ? `${host} / ${tail}` : tail;
  } catch {
    return raw;
  }
}

function normalizeMessageSources(sources) {
  const output = [];
  const seen = new Set();
  for (const source of sources || []) {
    if (!source || typeof source !== "object") continue;
    if (!sourceEligibleForDisplay(source)) continue;
    const key = canonicalSourceKey(source);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    output.push({
      ...source,
      canonical_key: key,
      label: sourceLabelFromUrl(source),
      link: source.url || source.url_canonical || "",
    });
  }
  return output;
}

function sourceIsProcedure(source) {
  if (!source || typeof source !== "object") return false;
  if (Boolean(source?.has_tab_steps)) return true;
  const url = String(source?.url || "").trim();
  const canonical = String(source?.url_canonical || "").trim();
  return TAB_STEP_FRAGMENT_RE.test(url) || TAB_STEP_FRAGMENT_RE.test(canonical);
}

function sourceEligibleByRawScores(source) {
  const vRaw = Number(source?.vector_score_raw);
  const bRaw = Number(source?.bm25_score_raw);
  if (!Number.isFinite(vRaw) && !Number.isFinite(bRaw)) return false;
  return vRaw > LEGACY_SOURCE_MIN_V_RAW || bRaw > LEGACY_SOURCE_MIN_B_RAW;
}

function sourceEligibleForDisplay(source) {
  if (!source || typeof source !== "object") return false;
  if (Object.prototype.hasOwnProperty.call(source, "source_score_eligible")) {
    return Boolean(source?.source_score_eligible);
  }
  return sourceEligibleByRawScores(source);
}

function stripTrailingProcedureLinksMarkdown(content) {
  const raw = String(content || "");
  return raw.replace(PROCEDURE_LINKS_TRAILING_BLOCK_RE, "");
}

function replaceAnchorWithText(anchor) {
  if (!anchor || !anchor.parentNode) return;
  const text = anchor.textContent || "";
  anchor.replaceWith(document.createTextNode(text));
}

function sanitizeMarkdownAnchorsToSources(wrapper, sources = []) {
  const allowedSources = normalizeMessageSources(sources);
  const allowedKeys = new Set(allowedSources.map((source) => String(source?.canonical_key || "").trim()));
  const linkedSourceKeys = new Set();
  let keptLinkCount = 0;

  for (const anchor of wrapper.querySelectorAll("a[href]")) {
    const rawHref = String(anchor.getAttribute("href") || "").trim();
    const key = canonicalSourceKey({ url: rawHref });
    if (!key || !allowedKeys.has(key) || keptLinkCount >= INLINE_SOURCE_LINKS_MAX) {
      replaceAnchorWithText(anchor);
      continue;
    }
    keptLinkCount += 1;
    linkedSourceKeys.add(key);
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noreferrer");
  }

  return Array.from(linkedSourceKeys);
}

function renderMarkdownWithSourceLinkBehavior(content, sources = []) {
  const rawContent = String(content || "");
  const cleanedContent = stripTrailingProcedureLinksMarkdown(rawContent);
  const html = String(marked.parse(cleanedContent || ""));
  if (typeof document === "undefined") {
    return { html, linkedSourceKeys: [] };
  }
  const wrapper = document.createElement("div");
  wrapper.innerHTML = html;
  const linkedSourceKeys = sanitizeMarkdownAnchorsToSources(wrapper, sources);
  return { html: wrapper.innerHTML, linkedSourceKeys };
}

function parseTimestamp(value) {
  if (!value || typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;

  if (ISO_TZ_SUFFIX_RE.test(raw)) {
    const zoned = new Date(raw);
    return Number.isNaN(zoned.getTime()) ? null : zoned;
  }

  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const utc = new Date(`${normalized}Z`);
  if (!Number.isNaN(utc.getTime())) {
    return utc;
  }

  const fallback = new Date(raw);
  return Number.isNaN(fallback.getTime()) ? null : fallback;
}

function formatDateTimeEst(value) {
  if (!value || typeof value !== "string") return "unknown";
  const parsed = parseTimestamp(value);
  if (!parsed) {
    return value;
  }
  return parsed.toLocaleString("en-US", {
    timeZone: CHAT_TIME_ZONE,
    timeZoneName: "short",
  });
}

function formatLastScraped(value) {
  return formatDateTimeEst(value);
}

function formatMessageDateTime(value) {
  return formatDateTimeEst(value);
}

function formatMessageVersion(value) {
  if (!value || typeof value !== "string") return "unknown";
  const clean = value.trim();
  if (!clean) return "unknown";
  return clean.toLowerCase().startsWith("v") ? clean : `v${clean}`;
}

function sourceHoverTitle(source) {
  const domain = domainFromUrl(source.link || source.url || source.url_canonical || "");
  const label = source.label || domain || source.link || source.url || source.url_canonical || "source";
  const link = source.link || source.url || source.url_canonical || "";
  const lastScraped = formatLastScraped(source.last_scraped);
  return `${label}\n${link}\nLast scraped: ${lastScraped}`;
}

function releaseHoverTitle(releaseInfo) {
  const lastCrawled = formatLastScraped(releaseInfo?.lastCrawled || "");
  return `View changelog\nLast crawled: ${lastCrawled}`;
}

function scoringGuideHref() {
  const guidePath = "/connections/reference/hybrid-retrieval";
  try {
    if (document.referrer) {
      const referrer = new URL(document.referrer);
      if (referrer.pathname.startsWith("/connections")) {
        return `${referrer.origin}${guidePath}`;
      }
    }
  } catch {
    // Fall through to local paths.
  }

  if (window.location.pathname.startsWith("/connections")) {
    return guidePath;
  }

  return "/reference/hybrid-retrieval";
}

export function ChatApp() {
  const guideHref = scoringGuideHref();
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
  const [releaseInfo, setReleaseInfo] = useState(null);
  const [releaseLoadFailed, setReleaseLoadFailed] = useState(false);
  const [sourceExpandedByMessage, setSourceExpandedByMessage] = useState({});
  const [feedbackByMessage, setFeedbackByMessage] = useState({});
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
    setFeedbackByMessage({});
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
    const response = await apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, rating }),
    });
    if (!response.ok) {
      throw new Error("Failed to record feedback");
    }
  };

  const toggleFeedback = async (messageId, rating) => {
    const current = Number(feedbackByMessage?.[messageId] || 0);
    const next = current === rating ? 0 : rating;
    setFeedbackByMessage((prev) => ({
      ...prev,
      [messageId]: next,
    }));
    if (next === 0) {
      return;
    }
    try {
      await sendFeedback(messageId, next);
    } catch {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: current,
      }));
    }
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
          m.id === assistantId
            ? { ...m, content: STREAM_ERROR_FALLBACK_MESSAGE }
            : m
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
          const errorText =
            String(payload?.error || "").trim() || STREAM_ERROR_FALLBACK_MESSAGE;
          if (!fullText.trim()) {
            fullText = errorText;
          }
          updateAssistant({ content: fullText });
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
        created_at: data.user_created_at || new Date().toISOString(),
        question_created_at: data.user_created_at || null,
        app_version: data.app_version || "",
      };
      const assistantMsg = {
        id: data.assistant_message_id,
        role: "assistant",
        content: "",
        sources: [],
        has_debug: false,
        created_at:
          data.assistant_created_at || data.user_created_at || new Date().toISOString(),
        question_created_at: data.question_created_at || data.user_created_at || null,
        app_version: data.app_version || "",
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
    let cancelled = false;

    const loadReleaseInfo = async () => {
      try {
        const res = await apiFetch("/release");
        if (!res.ok) {
          throw new Error("Release info unavailable");
        }

        const data = await res.json();
        const version = typeof data.version === "string" ? data.version.trim() : "";
        const changelogUrl =
          typeof data.changelog_url === "string" && data.changelog_url.trim()
            ? data.changelog_url.trim()
            : CHANGELOG_FALLBACK_HREF;
        const lastCrawled =
          typeof data.last_crawled === "string" ? data.last_crawled.trim() : "";

        if (!cancelled) {
          if (version) {
            setReleaseInfo({ version, changelogUrl, lastCrawled });
            setReleaseLoadFailed(false);
          } else {
            setReleaseInfo(null);
            setReleaseLoadFailed(true);
          }
        }
      } catch {
        if (!cancelled) {
          setReleaseInfo(null);
          setReleaseLoadFailed(true);
        }
      }
    };

    loadReleaseInfo();
    return () => {
      cancelled = true;
    };
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
    setSourceExpandedByMessage({});
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

  const assistantSourceMeta = {};
  let previousAssistantSourceKeys = null;
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    const displaySources = normalizeMessageSources(message.sources || []);
    const keys = displaySources.map((source) => source.canonical_key);
    const overlapPrevCount =
      previousAssistantSourceKeys === null
        ? null
        : keys.filter((key) => previousAssistantSourceKeys.has(key)).length;
    assistantSourceMeta[message.id] = {
      displaySources,
      sourceCount: keys.length,
      overlapPrevCount,
    };
    previousAssistantSourceKeys = new Set(keys);
  }
  let latestAssistantWithSourcesId = null;
  for (const message of messages) {
    if (message.role !== "assistant") continue;
    const sourceCount = assistantSourceMeta[message.id]?.sourceCount || 0;
    if (sourceCount > 0) {
      latestAssistantWithSourcesId = message.id;
    }
  }

  if (debugMessageId) {
    const debugPayload = debugState.data?.debug || {};
    const retrieval = debugPayload.retrieval || {};
    const rankedChunks = retrieval.ranked_chunks || [];
    const sources = debugPayload.sources || [];
    const llmRequest = debugPayload.llm_request || {};
    const responseText = debugPayload.llm_response_text || "";
    const queryEffective = debugPayload.query_effective || debugPayload.query || "";
    const queryRewritten = debugPayload.query_rewritten || "";
    const queryRewrite = debugPayload.query_rewrite || {};
    const rewriteUsed = Boolean(queryRewrite.used);
    const rewriteReason = queryRewrite.reason || (rewriteUsed ? "used" : "not_available");
    const rewriteModel = queryRewrite.model || "-";
    const rewriteHistoryTurns = queryRewrite.history_turns ?? "-";
    const rewriteError = queryRewrite.error || "";

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
                    href={guideHref}
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
                  <h2 className="card-title text-base">Query Rewrite</h2>
                  <div className="overflow-x-auto">
                    <table className="table table-sm">
                      <tbody>
                        <tr>
                          <th>Status</th>
                          <td>{rewriteUsed ? "rewritten" : "not rewritten"}</td>
                        </tr>
                        <tr>
                          <th>Reason</th>
                          <td>{rewriteReason}</td>
                        </tr>
                        <tr>
                          <th>Model</th>
                          <td>{rewriteModel}</td>
                        </tr>
                        <tr>
                          <th>History turns</th>
                          <td>{String(rewriteHistoryTurns)}</td>
                        </tr>
                        {rewriteError ? (
                          <tr>
                            <th>Error</th>
                            <td className="text-error">{rewriteError}</td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <h3 className="font-semibold text-sm mt-2">Effective Query Used for Retrieval</h3>
                  <pre className="debug-pre">{queryEffective || "-"}</pre>
                  {queryRewritten ? (
                    <>
                      <h3 className="font-semibold text-sm mt-3">Rewritten Standalone Query</h3>
                      <pre className="debug-pre">{queryRewritten}</pre>
                    </>
                  ) : null}
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
                          <tr
                            key={`${source.extract_id || idx}-${idx}`}
                            className={sourceIsProcedure(source) ? "bg-amber-100/35" : ""}
                          >
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
          <div className="ml-auto flex items-center gap-2">
            {releaseInfo ? (
              <a
                className="btn btn-ghost btn-sm normal-case"
                href={releaseInfo.changelogUrl}
                target="_blank"
                rel="noreferrer"
                title={releaseHoverTitle(releaseInfo)}
              >
                v{releaseInfo.version}
              </a>
            ) : null}
            {!releaseInfo && releaseLoadFailed ? (
              <a
                className="btn btn-ghost btn-sm normal-case"
                href={CHANGELOG_FALLBACK_HREF}
                target="_blank"
                rel="noreferrer"
                title="View changelog"
              >
                Changelog
              </a>
            ) : null}
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
                  messages.map((msg) => {
                    const sourceInfo = assistantSourceMeta[msg.id] || {
                      displaySources: [],
                      sourceCount: 0,
                      overlapPrevCount: null,
                    };
                    const hasSources = sourceInfo.sourceCount > 0;
                    const hasExplicitExpandState = Object.prototype.hasOwnProperty.call(
                      sourceExpandedByMessage,
                      msg.id
                    );
                    const isExpanded = hasSources
                      ? hasExplicitExpandState
                        ? Boolean(sourceExpandedByMessage[msg.id])
                        : msg.id === latestAssistantWithSourcesId
                      : false;
                    const sourceListId = `assistant-sources-${msg.id}`;
                    const askedAtText = formatMessageDateTime(
                      msg.question_created_at || msg.created_at || ""
                    );
                    const versionText = formatMessageVersion(msg.app_version || "");
                    const selectedFeedback = Number(feedbackByMessage?.[msg.id] || 0);
                    const upSelected = selectedFeedback === 1;
                    const downSelected = selectedFeedback === -1;
                    const renderedMarkdown = renderMarkdownWithSourceLinkBehavior(
                      msg.content || "",
                      msg.sources || []
                    );
                    const linkedSourceKeySet = new Set(renderedMarkdown.linkedSourceKeys || []);
                    return (
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
                                __html: renderedMarkdown.html,
                              }}
                            />
                            {hasSources && (
                              <button
                                className="btn btn-ghost btn-xs h-auto min-h-0 px-1 py-1 mt-2 normal-case text-xs opacity-80 hover:opacity-100"
                                type="button"
                                aria-expanded={isExpanded}
                                aria-controls={sourceListId}
                                onClick={() =>
                                  setSourceExpandedByMessage((prev) => ({
                                    ...prev,
                                    [msg.id]: !isExpanded,
                                  }))
                                }
                              >
                                {isExpanded ? (
                                  <ChevronDown className="size-3.5" aria-hidden="true" />
                                ) : (
                                  <ChevronRight className="size-3.5" aria-hidden="true" />
                                )}
                                <span>
                                  Sources: {sourceInfo.sourceCount} (procedures in amber) | Asked: {askedAtText} | Version:{" "}
                                  {versionText}
                                  {sourceInfo.overlapPrevCount !== null
                                    ? ` | Reused from previous answer: ${sourceInfo.overlapPrevCount}`
                                    : ""}
                                </span>
                              </button>
                            )}
                            {hasSources && isExpanded && (
                              <div id={sourceListId} className="flex flex-wrap gap-2 mt-2">
                                {sourceInfo.displaySources.map((source, idx) => (
                                  <a
                                    key={`${source.canonical_key}-${idx}`}
                                    href={source.link}
                                    target="_blank"
                                    rel="noreferrer"
                                    className={`badge gap-1 py-3 px-2 text-xs leading-tight ${
                                      sourceIsProcedure(source)
                                        ? "bg-amber-100/45 border-black/60 text-base-content"
                                        : "badge-outline"
                                    }`}
                                    title={sourceHoverTitle(source)}
                                  >
                                    <span
                                      className={`font-medium ${
                                        linkedSourceKeySet.has(source.canonical_key) ? "underline" : ""
                                      }`}
                                    >
                                      {source.label}
                                    </span>
                                  </a>
                                ))}
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
                                className={`btn btn-square btn-ghost ${upSelected ? "bg-success/15 text-success" : ""}`}
                                onClick={() => toggleFeedback(msg.id, 1)}
                                type="button"
                                aria-pressed={upSelected}
                              >
                                <ThumbsUp className={`size-[1.2em] ${upSelected ? "fill-current" : ""}`} />
                              </button>
                              <button
                                className={`btn btn-square btn-ghost ${downSelected ? "bg-error/15 text-error" : ""}`}
                                onClick={() => toggleFeedback(msg.id, -1)}
                                type="button"
                                aria-pressed={downSelected}
                              >
                                <ThumbsDown className={`size-[1.2em] ${downSelected ? "fill-current" : ""}`} />
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })
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

