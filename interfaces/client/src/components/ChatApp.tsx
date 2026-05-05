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

import { ThemeDropdown } from "./ThemeDropdown";

const API_BASE =
  import.meta.env.VITE_API_BASE ||
  `${String(import.meta.env.BASE_URL || "/").replace(/\/$/, "")}/api`;
const CHANGELOG_FALLBACK_HREF = "/connections/reference/changelog";
const STREAM_ERROR_FALLBACK_MESSAGE =
  "I hit a temporary problem generating a response. Please try again.";
const CHAT_TIME_ZONE = "America/New_York";
const ISO_TZ_SUFFIX_RE = /(?:[zZ]|[+\-]\d{2}:\d{2})$/;
const INLINE_SOURCE_LINKS_MAX = 2;
const LONG_LIST_COLLAPSE_MIN_ITEMS = 8;
const SIDEBAR_CHAT_PREVIEW_MAX = 40;
const SIDEBAR_CHAT_PREVIEW_WORD_BREAK_MIN = 24;
const USER_ID_MIN_LENGTH = 5;
const USER_ID_MAX_LENGTH = 7;
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

function parseViewMode() {
  const params = new URLSearchParams(window.location.search);
  const raw = String(params.get("view") || "").trim().toLowerCase();
  if (raw === "admin-stats" || raw === "admin-review" || raw === "admin-sources") {
    return raw;
  }
  return "chat";
}

function setViewParam(view) {
  const url = new URL(window.location.href);
  if (!view || view === "chat") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", view);
  }
  window.history.pushState({}, "", url);
}

function parseReviewMessageId() {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("review_message_id");
  const parsed = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function setReviewMessageParam(messageId) {
  const url = new URL(window.location.href);
  if (!messageId) {
    url.searchParams.delete("review_message_id");
  } else {
    url.searchParams.set("review_message_id", String(messageId));
  }
  window.history.pushState({}, "", url);
}

function normalizeUserId(value) {
  const cleaned = String(value || "")
    .replace(/[^a-z0-9]/gi, "")
    .toUpperCase();
  return cleaned.slice(0, USER_ID_MAX_LENGTH);
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

function omitMapKey(map, key) {
  if (!map || !Object.prototype.hasOwnProperty.call(map, key)) {
    return map || {};
  }
  const next = { ...map };
  delete next[key];
  return next;
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

function collapseLongMarkdownLists(wrapper, minItems = LONG_LIST_COLLAPSE_MIN_ITEMS) {
  if (!wrapper || typeof wrapper.querySelectorAll !== "function") return;

  const lists = Array.from(wrapper.querySelectorAll("ol, ul"));
  for (const list of lists) {
    if (!list || !list.parentNode) continue;
    if (list.closest("details")) continue;
    if (list.parentElement?.closest("ol, ul")) continue;

    const itemCount = Array.from(list.children || []).filter(
      (child) => child?.tagName?.toLowerCase() === "li"
    ).length;
    if (itemCount < minItems) continue;

    const details = document.createElement("details");
    details.className = "collapsible-list";

    const summary = document.createElement("summary");
    summary.textContent = list.tagName.toLowerCase() === "ol"
      ? `Show ${itemCount} steps`
      : `Show ${itemCount} items`;
    details.appendChild(summary);

    list.parentNode.insertBefore(details, list);
    details.appendChild(list);
  }
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
  collapseLongMarkdownLists(wrapper);
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

function formatSidebarChatPreview(value) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= SIDEBAR_CHAT_PREVIEW_MAX) {
    return normalized;
  }
  const candidate = normalized.slice(0, SIDEBAR_CHAT_PREVIEW_MAX + 1);
  const breakIndex = candidate.lastIndexOf(" ");
  const clipped =
    breakIndex >= SIDEBAR_CHAT_PREVIEW_WORD_BREAK_MIN
      ? candidate.slice(0, breakIndex)
      : normalized.slice(0, SIDEBAR_CHAT_PREVIEW_MAX);
  return `${clipped.trimEnd()}\u2026`;
}

function toDateTimeLocalValue(value) {
  if (!value || typeof value !== "string") return "";
  const parsed = parseTimestamp(value);
  if (!parsed) return "";

  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: CHAT_TIME_ZONE,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(parsed);

  const lookup = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${lookup.year || "0000"}-${lookup.month || "00"}-${lookup.day || "00"}T${lookup.hour || "00"}:${lookup.minute || "00"}`;
}

function formatRatingLabel(value) {
  if (Number(value) === 1) return "Positive";
  if (Number(value) === -1) return "Negative";
  return "Unrated";
}

function ratingBadgeClass(value) {
  if (Number(value) === 1) return "badge-success";
  if (Number(value) === -1) return "badge-error";
  return "badge-ghost";
}

function interactionAnswerExcerpt(value, max = 120) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  if (normalized.length <= max) return normalized;
  return `${normalized.slice(0, max - 1).trimEnd()}\u2026`;
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
  const [viewMode, setViewMode] = useState(parseViewMode());
  const [reviewMessageId, setReviewMessageId] = useState(parseReviewMessageId());
  const [debugMessageId, setDebugMessageId] = useState(parseDebugMessageId());
  const [debugState, setDebugState] = useState({
    loading: false,
    error: "",
    data: null,
  });
  const [adminStatsState, setAdminStatsState] = useState({
    loading: false,
    error: "",
    data: null,
  });
  const [adminReviewState, setAdminReviewState] = useState({
    loading: false,
    error: "",
    data: null,
  });
  const [adminDetailState, setAdminDetailState] = useState({
    loading: false,
    error: "",
    data: null,
  });
  const [sourceProposalState, setSourceProposalState] = useState({
    loading: false,
    error: "",
    proposals: [],
    selected: null,
    urls: [],
  });
  const [sourceProposalName, setSourceProposalName] = useState("Source test set");
  const [sourceProposalUrl, setSourceProposalUrl] = useState("");
  const [sourceProposalUrlAction, setSourceProposalUrlAction] = useState("add");
  const [sourceProposalBusy, setSourceProposalBusy] = useState(false);
  const [sourceTestQuery, setSourceTestQuery] = useState("");
  const [sourceTestResult, setSourceTestResult] = useState(null);
  const [statsFilters, setStatsFilters] = useState({
    range: "30d",
    start: "",
    end: "",
    user_id_search: "",
    sort: "last_interaction_at:desc",
    pilot_only: false,
    page: 1,
    page_size: 25,
  });
  const [reviewFilters, setReviewFilters] = useState({
    range: "30d",
    start: "",
    end: "",
    user_id: "",
    rating: "all",
    search: "",
    page: 1,
    page_size: 25,
  });
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [profile, setProfile] = useState(null);
  const [authUserId, setAuthUserId] = useState(
    () => normalizeUserId(window.localStorage.getItem("userId") || "")
  );
  const [loginInput, setLoginInput] = useState(
    () => normalizeUserId(window.localStorage.getItem("userId") || "")
  );
  const [loginError, setLoginError] = useState("");
  const [authChecking, setAuthChecking] = useState(
    () => Boolean(normalizeUserId(window.localStorage.getItem("userId") || ""))
  );
  const [releaseInfo, setReleaseInfo] = useState(null);
  const [releaseLoadFailed, setReleaseLoadFailed] = useState(false);
  const [sourceExpandedByMessage, setSourceExpandedByMessage] = useState({});
  const [sourcePreviewState, setSourcePreviewState] = useState({
    open: false,
    loading: false,
    error: "",
    title: "",
    url: "",
    html: "",
    lastScraped: "",
  });
  const [feedbackByMessage, setFeedbackByMessage] = useState({});
  const [negativeFeedbackDraftByMessage, setNegativeFeedbackDraftByMessage] = useState({});
  const [negativeFeedbackPendingByMessage, setNegativeFeedbackPendingByMessage] = useState({});
  const [submittingNegativeByMessage, setSubmittingNegativeByMessage] = useState({});
  const [negativeFeedbackErrorByMessage, setNegativeFeedbackErrorByMessage] = useState({});
  const [activeNegativeFeedbackMessageId, setActiveNegativeFeedbackMessageId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const listRef = useRef(null);
  const searchRef = useRef(null);
  const inputRef = useRef(null);

  const apiFetch = useCallback(
    (path, options = {}) => {
      const headers = new Headers(options.headers || {});
      if (authUserId) {
        headers.set("X-User-ID", authUserId);
      }
      return fetch(`${API_BASE}${path}`, { ...options, headers });
    },
    [authUserId]
  );

  const fetchProfileForUser = useCallback(async (candidate) => {
    const normalized = normalizeUserId(candidate);
    const headers = new Headers({ "X-User-ID": normalized });
    const response = await fetch(`${API_BASE}/profile`, { headers });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Sign-in failed");
    }
    const data = await response.json();
    return { normalized, data };
  }, []);

  const openView = useCallback((nextView) => {
    const normalized = nextView || "chat";
    setViewMode(normalized);
    setViewParam(normalized);
    if (normalized !== "admin-review") {
      setReviewMessageId(null);
      setReviewMessageParam(null);
    }
  }, []);

  const openReviewMessage = useCallback(
    (messageId) => {
      const parsed = Number(messageId);
      if (!Number.isFinite(parsed)) return;
      if (viewMode !== "admin-review") {
        openView("admin-review");
      }
      setReviewMessageId(parsed);
      setReviewMessageParam(parsed);
    },
    [openView, viewMode]
  );

  const closeReviewMessage = useCallback(() => {
    setReviewMessageId(null);
    setAdminDetailState({ loading: false, error: "", data: null });
    setReviewMessageParam(null);
  }, []);

  const openReviewForUser = useCallback(
    (userId) => {
      setReviewFilters((prev) => ({
        ...prev,
        user_id: String(userId || ""),
        page: 1,
      }));
      closeReviewMessage();
      openView("admin-review");
    },
    [closeReviewMessage, openView]
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
    if (!authUserId) {
      setChats([]);
      return;
    }
    const res = await apiFetch("/chats");
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    const items = data.chats || [];
    setChats(items);
    if (!activeChatId && items.length) {
      setActiveChatId(items[0].id);
    }
  };

  const loadMessages = async (chatId) => {
    if (!chatId || !authUserId) return;
    const res = await apiFetch(`/chats/${chatId}/messages?limit=50`);
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    setMessages(data.messages || []);
    setFeedbackByMessage({});
    setNegativeFeedbackDraftByMessage({});
    setNegativeFeedbackPendingByMessage({});
    setSubmittingNegativeByMessage({});
    setNegativeFeedbackErrorByMessage({});
    setActiveNegativeFeedbackMessageId(null);
    scrollToBottom(true);
  };

  const loadAdminStats = useCallback(async () => {
    const params = new URLSearchParams();
    const nextFilters = statsFilters;
    params.set("range", nextFilters.range);
    if (nextFilters.start) params.set("start", nextFilters.start);
    if (nextFilters.end) params.set("end", nextFilters.end);
    if (nextFilters.user_id_search) params.set("user_id_search", nextFilters.user_id_search);
    if (nextFilters.sort) params.set("sort", nextFilters.sort);
    if (nextFilters.pilot_only) params.set("pilot_only", "true");
    params.set("page", String(nextFilters.page || 1));
    params.set("page_size", String(nextFilters.page_size || 25));

    setAdminStatsState((prev) => ({ ...prev, loading: true, error: "" }));
    try {
      const res = await apiFetch(`/admin/stats/users?${params.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to load admin stats");
      }
      const data = await res.json();
      setAdminStatsState({ loading: false, error: "", data });
    } catch (error) {
      setAdminStatsState({
        loading: false,
        error: error?.message || "Failed to load admin stats",
        data: null,
      });
    }
  }, [apiFetch, statsFilters]);

  const loadAdminReview = useCallback(async () => {
    const params = new URLSearchParams();
    const nextFilters = reviewFilters;
    params.set("range", nextFilters.range);
    if (nextFilters.start) params.set("start", nextFilters.start);
    if (nextFilters.end) params.set("end", nextFilters.end);
    if (nextFilters.user_id) params.set("user_id", nextFilters.user_id);
    if (nextFilters.rating) params.set("rating", nextFilters.rating);
    if (nextFilters.search) params.set("search", nextFilters.search);
    params.set("page", String(nextFilters.page || 1));
    params.set("page_size", String(nextFilters.page_size || 25));

    setAdminReviewState((prev) => ({ ...prev, loading: true, error: "" }));
    try {
      const res = await apiFetch(`/admin/interactions?${params.toString()}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to load interactions");
      }
      const data = await res.json();
      setAdminReviewState({ loading: false, error: "", data });
    } catch (error) {
      setAdminReviewState({
        loading: false,
        error: error?.message || "Failed to load interactions",
        data: null,
      });
    }
  }, [apiFetch, reviewFilters]);

  const loadSourceProposalDetail = useCallback(
    async (proposalId) => {
      if (!proposalId) return null;
      const res = await apiFetch(`/admin/source-proposals/${proposalId}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to load source test set");
      }
      const data = await res.json();
      setSourceProposalState((prev) => ({
        ...prev,
        selected: data.proposal || null,
        urls: data.urls || [],
      }));
      return data;
    },
    [apiFetch]
  );

  const loadSourceProposals = useCallback(async () => {
    setSourceProposalState((prev) => ({ ...prev, loading: true, error: "" }));
    try {
      const res = await apiFetch("/admin/source-proposals");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to load source test sets");
      }
      const data = await res.json();
      const proposals = data.proposals || [];
      const selectedId = sourceProposalState.selected?.id || proposals[0]?.id || null;
      setSourceProposalState((prev) => ({
        ...prev,
        loading: false,
        error: "",
        proposals,
        selected: selectedId ? prev.selected : null,
        urls: selectedId ? prev.urls : [],
      }));
      if (selectedId) {
        await loadSourceProposalDetail(selectedId);
      }
    } catch (error) {
      setSourceProposalState((prev) => ({
        ...prev,
        loading: false,
        error: error?.message || "Failed to load source test sets",
      }));
    }
  }, [apiFetch, loadSourceProposalDetail, sourceProposalState.selected?.id]);

  const createSourceProposal = useCallback(async () => {
    setSourceProposalBusy(true);
    setSourceTestResult(null);
    try {
      const res = await apiFetch("/admin/source-proposals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: sourceProposalName || "Source test set" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to create source test set");
      }
      const data = await res.json();
      setSourceProposalState((prev) => ({
        ...prev,
        error: "",
        selected: data.proposal || null,
        proposals: [data.proposal, ...prev.proposals.filter((p) => p.id !== data.proposal?.id)].filter(Boolean),
        urls: [],
      }));
    } catch (error) {
      setSourceProposalState((prev) => ({ ...prev, error: error?.message || "Failed to create source test set" }));
    } finally {
      setSourceProposalBusy(false);
    }
  }, [apiFetch, sourceProposalName]);

  const submitSourceProposalUrl = useCallback(async () => {
    const proposalId = sourceProposalState.selected?.id;
    const url = sourceProposalUrl.trim();
    if (!proposalId || !url) return;
    setSourceProposalBusy(true);
    setSourceTestResult(null);
    try {
      const res = await apiFetch(`/admin/source-proposals/${proposalId}/urls`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, action: sourceProposalUrlAction }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to update source URL");
      }
      const data = await res.json();
      setSourceProposalUrl("");
      setSourceProposalState((prev) => ({
        ...prev,
        error: "",
        selected: data.proposal || prev.selected,
        urls: data.urls || prev.urls,
      }));
      loadSourceProposals();
    } catch (error) {
      setSourceProposalState((prev) => ({ ...prev, error: error?.message || "Failed to update source URL" }));
    } finally {
      setSourceProposalBusy(false);
    }
  }, [apiFetch, loadSourceProposals, sourceProposalState.selected?.id, sourceProposalUrl, sourceProposalUrlAction]);

  const refreshSourceProposal = useCallback(async () => {
    const proposalId = sourceProposalState.selected?.id;
    if (!proposalId) return;
    setSourceProposalBusy(true);
    setSourceTestResult(null);
    try {
      const res = await apiFetch(`/admin/source-proposals/${proposalId}/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wait: false }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to queue refresh");
      }
      const data = await res.json();
      setSourceProposalState((prev) => ({ ...prev, error: "", selected: data.proposal || prev.selected }));
      loadSourceProposals();
    } catch (error) {
      setSourceProposalState((prev) => ({ ...prev, error: error?.message || "Failed to queue refresh" }));
    } finally {
      setSourceProposalBusy(false);
    }
  }, [apiFetch, loadSourceProposals, sourceProposalState.selected?.id]);

  const runSourceTestQuery = useCallback(async () => {
    const proposalId = sourceProposalState.selected?.id;
    const query = sourceTestQuery.trim();
    if (!proposalId || !query) return;
    setSourceProposalBusy(true);
    setSourceTestResult(null);
    try {
      const res = await apiFetch(`/admin/source-proposals/${proposalId}/test-query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, compare_to_live: true }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to test query");
      }
      const data = await res.json();
      setSourceProposalState((prev) => ({ ...prev, error: "" }));
      setSourceTestResult(data.result || null);
    } catch (error) {
      setSourceProposalState((prev) => ({ ...prev, error: error?.message || "Failed to test query" }));
    } finally {
      setSourceProposalBusy(false);
    }
  }, [apiFetch, sourceProposalState.selected?.id, sourceTestQuery]);

  const promoteSourceProposal = useCallback(async () => {
    const proposalId = sourceProposalState.selected?.id;
    if (!proposalId) return;
    const ok = window.confirm("Promote these source URL changes to live? New pages still require the next live refresh before they affect answers.");
    if (!ok) return;
    setSourceProposalBusy(true);
    try {
      const res = await apiFetch(`/admin/source-proposals/${proposalId}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || "Failed to promote source test set");
      }
      const data = await res.json();
      setSourceProposalState((prev) => ({ ...prev, error: "", selected: data.proposal || prev.selected }));
      loadSourceProposals();
    } catch (error) {
      setSourceProposalState((prev) => ({ ...prev, error: error?.message || "Failed to promote source test set" }));
    } finally {
      setSourceProposalBusy(false);
    }
  }, [apiFetch, loadSourceProposals, sourceProposalState.selected?.id]);

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

  const sendFeedback = async (messageId, rating, note = "") => {
    const response = await apiFetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, rating, note }),
    });
    if (!response.ok) {
      throw new Error("Failed to record feedback");
    }
  };

  const clearNegativeFeedbackState = (messageId, options = {}) => {
    const { clearDraft = false, clearPending = true, clearError = true } = options;
    setActiveNegativeFeedbackMessageId((prev) => (prev === messageId ? null : prev));
    if (clearDraft) {
      setNegativeFeedbackDraftByMessage((prev) => omitMapKey(prev, messageId));
    }
    if (clearPending) {
      setNegativeFeedbackPendingByMessage((prev) => omitMapKey(prev, messageId));
    }
    if (clearError) {
      setNegativeFeedbackErrorByMessage((prev) => omitMapKey(prev, messageId));
    }
    setSubmittingNegativeByMessage((prev) => omitMapKey(prev, messageId));
  };

  const submitPositiveFeedback = async (messageId) => {
    const current = Number(feedbackByMessage?.[messageId] || 0);
    const next = current === 1 ? 0 : 1;
    setFeedbackByMessage((prev) => ({
      ...prev,
      [messageId]: next,
    }));
    if (next === 0) {
      clearNegativeFeedbackState(messageId, { clearDraft: true });
      return;
    }
    clearNegativeFeedbackState(messageId, { clearDraft: true });
    try {
      await sendFeedback(messageId, next);
    } catch {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: current,
      }));
    }
  };

  const toggleNegativeFeedback = (messageId) => {
    const current = Number(feedbackByMessage?.[messageId] || 0);
    const isPending = Boolean(negativeFeedbackPendingByMessage?.[messageId]);
    if (current === -1 && isPending) {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: 0,
      }));
      clearNegativeFeedbackState(messageId, { clearDraft: true });
      return;
    }
    if (current === -1) {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: 0,
      }));
      clearNegativeFeedbackState(messageId, { clearDraft: true });
      return;
    }
    if (activeNegativeFeedbackMessageId && activeNegativeFeedbackMessageId !== messageId) {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [activeNegativeFeedbackMessageId]: 0,
      }));
      clearNegativeFeedbackState(activeNegativeFeedbackMessageId, { clearDraft: true });
    }
    setFeedbackByMessage((prev) => ({
      ...prev,
      [messageId]: -1,
    }));
    setNegativeFeedbackPendingByMessage((prev) => ({
      ...prev,
      [messageId]: true,
    }));
    setNegativeFeedbackDraftByMessage((prev) => ({
      ...prev,
      [messageId]: String(prev?.[messageId] || ""),
    }));
    setNegativeFeedbackErrorByMessage((prev) => omitMapKey(prev, messageId));
    setActiveNegativeFeedbackMessageId(messageId);
  };

  const submitNegativeFeedback = async (messageId, note = "") => {
    const current = Number(feedbackByMessage?.[messageId] || 0);
    const trimmedNote = String(note || "").trim();
    setSubmittingNegativeByMessage((prev) => ({
      ...prev,
      [messageId]: true,
    }));
    setNegativeFeedbackErrorByMessage((prev) => omitMapKey(prev, messageId));
    try {
      await sendFeedback(messageId, -1, trimmedNote);
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: -1,
      }));
      clearNegativeFeedbackState(messageId, { clearDraft: true });
    } catch {
      setFeedbackByMessage((prev) => ({
        ...prev,
        [messageId]: current,
      }));
      setNegativeFeedbackPendingByMessage((prev) => ({
        ...prev,
        [messageId]: true,
      }));
      setNegativeFeedbackErrorByMessage((prev) => ({
        ...prev,
        [messageId]: "Could not record feedback. Please try again.",
      }));
      setActiveNegativeFeedbackMessageId(messageId);
      setSubmittingNegativeByMessage((prev) => omitMapKey(prev, messageId));
    }
  };

  const handleNegativeFeedbackSubmit = async (messageId) => {
    const draft = String(negativeFeedbackDraftByMessage?.[messageId] || "");
    await submitNegativeFeedback(messageId, draft);
  };

  const handleNegativeFeedbackSkip = async (messageId) => {
    await submitNegativeFeedback(messageId, "");
  };

  const closeSourcePreview = useCallback(() => {
    setSourcePreviewState((prev) => ({
      ...prev,
      open: false,
      loading: false,
      error: "",
    }));
  }, []);

  const openSourcePreview = useCallback(
    async (source) => {
      const sourceUrl = String(
        source?.link || source?.url || source?.url_canonical || ""
      ).trim();
      if (!sourceUrl) return;

      const sourceTitle = String(source?.label || sourceUrl);
      setSourcePreviewState({
        open: true,
        loading: true,
        error: "",
        title: sourceTitle,
        url: sourceUrl,
        html: "",
        lastScraped: String(source?.last_scraped || ""),
      });

      try {
        const params = new URLSearchParams({ url: sourceUrl });
        const extractId = Number(source?.extract_id);
        if (Number.isFinite(extractId) && extractId > 0) {
          params.set("extract_id", String(extractId));
        }

        const response = await apiFetch(`/sources/preview?${params.toString()}`);
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || "Could not load source preview");
        }

        const payload = await response.json();
        setSourcePreviewState({
          open: true,
          loading: false,
          error: "",
          title: String(payload?.title || sourceTitle),
          url: String(payload?.url || sourceUrl),
          html: String(payload?.html || ""),
          lastScraped: String(payload?.last_scraped || source?.last_scraped || ""),
        });
      } catch (error) {
        setSourcePreviewState((prev) => ({
          ...prev,
          loading: false,
          error: error?.message || "Could not load source preview",
        }));
      }
    },
    [apiFetch]
  );

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

  const sendMessage = async (rawMessage) => {
    const message = String(rawMessage || "").trim();
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

  const buildCustomerExplanationPrompt = (question, answer) => {
    const safeQuestion = String(question || "").trim() || "(No customer question available)";
    const safeAnswer = String(answer || "").trim() || "(No answer available)";
    return `Please help me explain this customer interaction in a simple, easy-to-understand, friendly manner.

Use plain language, keep it concise, stay accurate to the answer, and avoid internal jargon. Do not mention internal systems, prompts, retrieval, or debug details.

Customer question:
${safeQuestion}

Answer to explain:
${safeAnswer}`;
  };

  const explainToCustomer = async (messageId) => {
    if (sending) return;
    const assistantIndex = messages.findIndex((message) => message.id === messageId);
    if (assistantIndex < 0) return;
    const assistantMessage = messages[assistantIndex];
    const userMessage = messages
      .slice(0, assistantIndex)
      .reverse()
      .find((message) => message.role === "user");
    const prompt = buildCustomerExplanationPrompt(
      userMessage?.content || "",
      assistantMessage?.content || ""
    );
    await sendMessage(prompt);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    await sendMessage(input);
  };

  useEffect(() => {
    let cancelled = false;

    const restoreAuth = async () => {
      if (!authUserId) {
        setAuthChecking(false);
        setProfile(null);
        return;
      }

      setAuthChecking(true);
      try {
        const { normalized, data } = await fetchProfileForUser(authUserId);
        if (cancelled) return;
        window.localStorage.setItem("userId", normalized);
        setAuthUserId(normalized);
        setLoginInput(normalized);
        setLoginError("");
        setProfile(data);
      } catch (error) {
        if (cancelled) return;
        window.localStorage.removeItem("userId");
        setAuthUserId("");
        setLoginInput("");
        setProfile(null);
        setLoginError(error?.message || "Sign-in failed");
      } finally {
        if (!cancelled) {
          setAuthChecking(false);
        }
      }
    };

    restoreAuth();
    return () => {
      cancelled = true;
    };
  }, [fetchProfileForUser]);

  useEffect(() => {
    if (!authUserId) {
      setChats([]);
      setMessages([]);
      setViewMode("chat");
      setReviewMessageId(null);
      return;
    }
    loadChats();
  }, [apiFetch, authUserId]);

  useEffect(() => {
    if (!profile?.is_admin && (viewMode === "admin-stats" || viewMode === "admin-review" || viewMode === "admin-sources")) {
      setViewMode("chat");
      setViewParam("chat");
      setReviewMessageId(null);
      setReviewMessageParam(null);
    }
  }, [profile, viewMode]);

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
    if (authUserId && activeChatId) {
      setChatParam(activeChatId);
      loadMessages(activeChatId);
    }
  }, [activeChatId, authUserId]);

  useEffect(() => {
    setSourceExpandedByMessage({});
    setSourcePreviewState((prev) =>
      prev.open
        ? {
          ...prev,
          open: false,
          loading: false,
          error: "",
        }
        : prev
    );
  }, [activeChatId]);

  useEffect(() => {
    const onPopState = () => {
      setActiveChatId(parseChatId());
      setViewMode(parseViewMode());
      setReviewMessageId(parseReviewMessageId());
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

  useEffect(() => {
    if (!authUserId || !profile?.is_admin || viewMode !== "admin-stats") return;
    loadAdminStats();
  }, [authUserId, profile, viewMode, loadAdminStats]);

  useEffect(() => {
    if (!authUserId || !profile?.is_admin || viewMode !== "admin-review") return;
    loadAdminReview();
  }, [authUserId, profile, viewMode, loadAdminReview]);

  useEffect(() => {
    if (!authUserId || !profile?.is_admin || viewMode !== "admin-sources") return;
    loadSourceProposals();
  }, [authUserId, profile, viewMode, loadSourceProposals]);

  useEffect(() => {
    if (!authUserId || !profile?.is_admin || viewMode !== "admin-review" || !reviewMessageId) {
      if (!reviewMessageId) {
        setAdminDetailState({ loading: false, error: "", data: null });
      }
      return;
    }
    let cancelled = false;
    const loadDetail = async () => {
      setAdminDetailState({ loading: true, error: "", data: null });
      try {
        const res = await apiFetch(`/admin/interactions/${reviewMessageId}`);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || "Failed to load interaction detail");
        }
        const data = await res.json();
        if (!cancelled) {
          setAdminDetailState({ loading: false, error: "", data });
        }
      } catch (error) {
        if (!cancelled) {
          setAdminDetailState({
            loading: false,
            error: error?.message || "Failed to load interaction detail",
            data: null,
          });
        }
      }
    };
    loadDetail();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, authUserId, profile, reviewMessageId, viewMode]);

  const clearLoginState = () => {
    window.localStorage.removeItem("userId");
    setAuthUserId("");
    setLoginInput("");
    setLoginError("");
    setProfile(null);
    setActiveChatId(null);
    setViewMode("chat");
    setReviewMessageId(null);
    setDebugMessageId(null);
    setMessages([]);
    setChats([]);
    setSourcePreviewState({
      open: false,
      loading: false,
      error: "",
      title: "",
      url: "",
      html: "",
      lastScraped: "",
    });
    setAdminStatsState({ loading: false, error: "", data: null });
    setAdminReviewState({ loading: false, error: "", data: null });
    setAdminDetailState({ loading: false, error: "", data: null });
    setSourceProposalState({ loading: false, error: "", proposals: [], selected: null, urls: [] });
    setSourceTestResult(null);
    setViewParam("chat");
    setReviewMessageParam(null);
    clearDebugParam();
  };

  const handleLoginSubmit = async (event) => {
    event.preventDefault();
    const normalized = normalizeUserId(loginInput);
    if (normalized.length < USER_ID_MIN_LENGTH || normalized.length > USER_ID_MAX_LENGTH) {
      setLoginError("Enter your 4+2 as 5 to 7 letters and numbers.");
      return;
    }

    setAuthChecking(true);
    try {
      const { data } = await fetchProfileForUser(normalized);
      window.localStorage.setItem("userId", normalized);
      setAuthUserId(normalized);
      setLoginInput(normalized);
      setLoginError("");
      setProfile(data);
      setActiveChatId(null);
      setMessages([]);
    } catch (error) {
      window.localStorage.removeItem("userId");
      setAuthUserId("");
      setProfile(null);
      setLoginError(error?.message || "Sign-in failed");
    } finally {
      setAuthChecking(false);
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
  const canViewAdmin = Boolean(profile?.is_admin);

  const renderPrimaryHeader = (title) => (
    <nav className="navbar w-full bg-base-300">
      <div className="px-4 text-xl font-semibold">{title}</div>
      <div className="ml-auto flex items-center gap-2">
        {canViewAdmin ? (
          <>
            <button
              className={`btn btn-sm ${viewMode === "chat" ? "btn-primary" : "btn-ghost"}`}
              type="button"
              onClick={() => openView("chat")}
            >
              Chat
            </button>
            <button
              className={`btn btn-sm ${viewMode === "admin-stats" ? "btn-primary" : "btn-ghost"}`}
              type="button"
              onClick={() => openView("admin-stats")}
            >
              Admin stats
            </button>
            <button
              className={`btn btn-sm ${viewMode === "admin-review" ? "btn-primary" : "btn-ghost"}`}
              type="button"
              onClick={() => openView("admin-review")}
            >
              Interaction review
            </button>
            <button
              className={`btn btn-sm ${viewMode === "admin-sources" ? "btn-primary" : "btn-ghost"}`}
              type="button"
              onClick={() => openView("admin-sources")}
            >
              Source tests
            </button>
          </>
        ) : null}
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
          <div className="flex items-center gap-2">
            <div className="avatar placeholder">
              <div
                className="h-9 min-w-16 rounded-full px-2 text-white flex items-center justify-center leading-none"
                style={{ backgroundColor: profile.avatar.color }}
              >
                <span className="text-[10px] font-semibold tracking-tight">
                  {profile.avatar.initials}
                </span>
              </div>
            </div>
            <button className="btn btn-ghost btn-sm" type="button" onClick={clearLoginState}>
              Switch user
            </button>
          </div>
        ) : null}
        <ThemeDropdown />
      </div>
    </nav>
  );

  if (!authUserId) {
    return (
      <div className="min-h-screen bg-base-200 p-4 md:p-6">
        <div className="fixed right-4 top-4 z-50">
          <ThemeDropdown />
        </div>
        <div className="mx-auto flex min-h-[70vh] max-w-md items-center">
          <div className="card w-full border border-base-300 bg-base-100 shadow-xl">
            <div className="card-body gap-4">
              <div>
                <h1 className="text-2xl font-semibold">Sign in</h1>
                <p className="text-sm opacity-70">
                  Enter your 4+2 to open your chat history. IP addresses stay on the server.
                </p>
              </div>
              <form className="flex flex-col gap-3" onSubmit={handleLoginSubmit}>
                <label className="form-control">
                  <span className="label-text text-sm font-medium">4+2</span>
                  <input
                    className="input input-bordered"
                    type="text"
                    inputMode="text"
                    autoCapitalize="characters"
                    autoComplete="username"
                    maxLength={USER_ID_MAX_LENGTH}
                    value={loginInput}
                    onChange={(event) => {
                      setLoginInput(normalizeUserId(event.target.value));
                      if (loginError) {
                        setLoginError("");
                      }
                    }}
                    placeholder="1234AB"
                    disabled={authChecking}
                  />
                </label>
                {loginError ? (
                  <div className="text-sm text-error">{loginError}</div>
                ) : null}
                <button className="btn btn-primary" type="submit" disabled={authChecking}>
                  {authChecking ? "Signing in..." : "Continue"}
                </button>
              </form>
              {releaseInfo ? (
                <a
                  className="link link-hover text-sm opacity-70"
                  href={releaseInfo.changelogUrl}
                  target="_blank"
                  rel="noreferrer"
                  title={releaseHoverTitle(releaseInfo)}
                >
                  Version {releaseInfo.version}
                </a>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (viewMode === "admin-stats" && canViewAdmin) {
    const statsPayload = adminStatsState.data || {};
    const statsSummary = statsPayload.summary || {};
    const statsRows = statsPayload.users || [];
    const statsPagination = statsPayload.pagination || {};

    return (
      <div className="min-h-screen bg-base-200">
        {renderPrimaryHeader("Admin Stats")}
        <div className="p-4 md:p-6">
          <div className="mx-auto max-w-7xl space-y-4">
            <div className="card bg-base-100 border border-base-300 shadow-sm">
              <div className="card-body gap-4">
                <div className="flex flex-wrap items-end gap-3">
                  <label className="form-control">
                    <span className="label-text text-sm">Range</span>
                    <select
                      className="select select-bordered"
                      value={statsFilters.range}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          range: event.target.value,
                          page: 1,
                        }))
                      }
                    >
                      <option value="24h">24h</option>
                      <option value="7d">7d</option>
                      <option value="30d">30d</option>
                      <option value="all">All</option>
                      <option value="custom">Custom</option>
                    </select>
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">Start (ET)</span>
                    <input
                      className="input input-bordered"
                      type="datetime-local"
                      value={statsFilters.start}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          start: event.target.value,
                          range: "custom",
                          page: 1,
                        }))
                      }
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">End (ET)</span>
                    <input
                      className="input input-bordered"
                      type="datetime-local"
                      value={statsFilters.end}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          end: event.target.value,
                          range: "custom",
                          page: 1,
                        }))
                      }
                    />
                  </label>
                  <label className="form-control grow min-w-52">
                    <span className="label-text text-sm">User 4+2</span>
                    <input
                      className="input input-bordered"
                      type="text"
                      value={statsFilters.user_id_search}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          user_id_search: normalizeUserId(event.target.value),
                          page: 1,
                        }))
                      }
                      placeholder="Filter by 4+2"
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">Pilot group only</span>
                    <input
                      className="checkbox checkbox-primary mt-3"
                      type="checkbox"
                      checked={statsFilters.pilot_only}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          pilot_only: event.target.checked,
                          page: 1,
                        }))
                      }
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">Sort</span>
                    <select
                      className="select select-bordered"
                      value={statsFilters.sort}
                      onChange={(event) =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          sort: event.target.value,
                          page: 1,
                        }))
                      }
                    >
                      <option value="last_interaction_at:desc">Latest activity</option>
                      <option value="question_count:desc">Most questions</option>
                      <option value="positive_feedback_count:desc">Most positive</option>
                      <option value="negative_feedback_count:desc">Most negative</option>
                      <option value="user_id:asc">User A-Z</option>
                    </select>
                  </label>
                </div>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Users</div><div className="text-2xl font-semibold">{statsSummary.user_count || 0}</div></div></div>
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Questions</div><div className="text-2xl font-semibold">{statsSummary.question_count || 0}</div></div></div>
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Interactions</div><div className="text-2xl font-semibold">{statsSummary.interaction_count || 0}</div></div></div>
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Positive</div><div className="text-2xl font-semibold text-success">{statsSummary.positive_feedback_count || 0}</div></div></div>
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Negative</div><div className="text-2xl font-semibold text-error">{statsSummary.negative_feedback_count || 0}</div></div></div>
              <div className="card bg-base-100 border border-base-300 shadow-sm"><div className="card-body p-4"><div className="text-sm opacity-70">Unrated</div><div className="text-2xl font-semibold">{statsSummary.unrated_interaction_count || 0}</div></div></div>
            </div>

            {adminStatsState.loading ? (
              <div className="alert"><span>Loading admin stats...</span></div>
            ) : null}
            {adminStatsState.error ? (
              <div className="alert alert-error"><span>{adminStatsState.error}</span></div>
            ) : null}

            <div className="card bg-base-100 border border-base-300 shadow-sm">
              <div className="card-body">
                <div className="overflow-x-auto">
                  <table className="table table-sm">
                    <thead>
                      <tr>
                        <th>User</th>
                        <th>Questions</th>
                        <th>Positive</th>
                        <th>Negative</th>
                        <th>Rated</th>
                        <th>Unrated</th>
                        <th>Last interaction</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {statsRows.map((row) => (
                        <tr key={row.user_id}>
                          <td className="font-medium">{row.user_id}</td>
                          <td>{row.question_count}</td>
                          <td>{row.positive_feedback_count}</td>
                          <td>{row.negative_feedback_count}</td>
                          <td>{row.rated_interaction_count}</td>
                          <td>{row.unrated_interaction_count}</td>
                          <td>{formatMessageDateTime(row.last_interaction_at || "")}</td>
                          <td>
                            <button
                              className="btn btn-sm"
                              type="button"
                              onClick={() => openReviewForUser(row.user_id)}
                            >
                              Review
                            </button>
                          </td>
                        </tr>
                      ))}
                      {!adminStatsState.loading && statsRows.length === 0 ? (
                        <tr>
                          <td colSpan={8} className="opacity-70">
                            No users match the current filters.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between gap-3 mt-3">
                  <div className="text-sm opacity-70">
                    Page {statsPagination.page || 1} of {statsPagination.total_pages || 1} | Total users:{" "}
                    {statsPagination.total || 0}
                  </div>
                  <div className="flex gap-2">
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={(statsPagination.page || 1) <= 1}
                      onClick={() =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          page: Math.max(1, (prev.page || 1) - 1),
                        }))
                      }
                    >
                      Previous
                    </button>
                    <button
                      className="btn btn-sm"
                      type="button"
                      disabled={(statsPagination.page || 1) >= (statsPagination.total_pages || 1)}
                      onClick={() =>
                        setStatsFilters((prev) => ({
                          ...prev,
                          page: (prev.page || 1) + 1,
                        }))
                      }
                    >
                      Next
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (viewMode === "admin-review" && canViewAdmin) {
    const reviewPayload = adminReviewState.data || {};
    const reviewRows = reviewPayload.interactions || [];
    const reviewPagination = reviewPayload.pagination || {};
    const detail = adminDetailState.data?.interaction || null;

    return (
      <div className="min-h-screen bg-base-200">
        {renderPrimaryHeader("Interaction Review")}
        <div className="p-4 md:p-6">
          <div className="mx-auto max-w-7xl space-y-4">
            <div className="card bg-base-100 border border-base-300 shadow-sm">
              <div className="card-body gap-4">
                <div className="flex flex-wrap items-end gap-3">
                  <label className="form-control">
                    <span className="label-text text-sm">Range</span>
                    <select
                      className="select select-bordered"
                      value={reviewFilters.range}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          range: event.target.value,
                          page: 1,
                        }))
                      }
                    >
                      <option value="24h">24h</option>
                      <option value="7d">7d</option>
                      <option value="30d">30d</option>
                      <option value="all">All</option>
                      <option value="custom">Custom</option>
                    </select>
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">Start (ET)</span>
                    <input
                      className="input input-bordered"
                      type="datetime-local"
                      value={reviewFilters.start}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          start: event.target.value,
                          range: "custom",
                          page: 1,
                        }))
                      }
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">End (ET)</span>
                    <input
                      className="input input-bordered"
                      type="datetime-local"
                      value={reviewFilters.end}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          end: event.target.value,
                          range: "custom",
                          page: 1,
                        }))
                      }
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">User 4+2</span>
                    <input
                      className="input input-bordered"
                      type="text"
                      value={reviewFilters.user_id}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          user_id: normalizeUserId(event.target.value),
                          page: 1,
                        }))
                      }
                      placeholder="Filter by 4+2"
                    />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-sm">Rating</span>
                    <select
                      className="select select-bordered"
                      value={reviewFilters.rating}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          rating: event.target.value,
                          page: 1,
                        }))
                      }
                    >
                      <option value="all">All</option>
                      <option value="positive">Positive</option>
                      <option value="negative">Negative</option>
                      <option value="unrated">Unrated</option>
                    </select>
                  </label>
                  <label className="form-control grow min-w-60">
                    <span className="label-text text-sm">Search</span>
                    <input
                      className="input input-bordered"
                      type="text"
                      value={reviewFilters.search}
                      onChange={(event) =>
                        setReviewFilters((prev) => ({
                          ...prev,
                          search: event.target.value,
                          page: 1,
                        }))
                      }
                      placeholder="Search question, rewrite, answer, note, or source"
                    />
                  </label>
                </div>
              </div>
            </div>

            {adminReviewState.loading ? (
              <div className="alert"><span>Loading interactions...</span></div>
            ) : null}
            {adminReviewState.error ? (
              <div className="alert alert-error"><span>{adminReviewState.error}</span></div>
            ) : null}

            <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(22rem,1fr)]">
              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <div className="overflow-x-auto">
                    <table className="table table-sm">
                      <thead>
                        <tr>
                          <th>User</th>
                          <th>Asked</th>
                          <th>Question</th>
                          <th>Answer</th>
                          <th>Rating</th>
                          <th>Sources</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reviewRows.map((row) => (
                          <tr
                            key={row.assistant_message_id}
                            className={Number(reviewMessageId) === Number(row.assistant_message_id) ? "active" : ""}
                          >
                            <td>
                              <button
                                className="link link-hover font-medium"
                                type="button"
                                onClick={() => openReviewMessage(row.assistant_message_id)}
                              >
                                {row.user_id}
                              </button>
                            </td>
                            <td>{formatMessageDateTime(row.asked_at || "")}</td>
                            <td className="max-w-xs whitespace-normal">{row.question || "-"}</td>
                            <td className="max-w-sm whitespace-normal">{interactionAnswerExcerpt(row.answer || "") || "-"}</td>
                            <td>
                              <span className={`badge ${ratingBadgeClass(row.rating)}`}>
                                {formatRatingLabel(row.rating)}
                              </span>
                            </td>
                            <td>{row.source_count || 0}</td>
                          </tr>
                        ))}
                        {!adminReviewState.loading && reviewRows.length === 0 ? (
                          <tr>
                            <td colSpan={6} className="opacity-70">
                              No interactions match the current filters.
                            </td>
                          </tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                  <div className="flex items-center justify-between gap-3 mt-3">
                    <div className="text-sm opacity-70">
                      Page {reviewPagination.page || 1} of {reviewPagination.total_pages || 1} | Total interactions:{" "}
                      {reviewPagination.total || 0}
                    </div>
                    <div className="flex gap-2">
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={(reviewPagination.page || 1) <= 1}
                        onClick={() =>
                          setReviewFilters((prev) => ({
                            ...prev,
                            page: Math.max(1, (prev.page || 1) - 1),
                          }))
                        }
                      >
                        Previous
                      </button>
                      <button
                        className="btn btn-sm"
                        type="button"
                        disabled={(reviewPagination.page || 1) >= (reviewPagination.total_pages || 1)}
                        onClick={() =>
                          setReviewFilters((prev) => ({
                            ...prev,
                            page: (prev.page || 1) + 1,
                          }))
                        }
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <div className="flex items-center justify-between gap-2">
                    <h2 className="card-title text-base">Interaction Detail</h2>
                    {reviewMessageId ? (
                      <button className="btn btn-ghost btn-sm" type="button" onClick={closeReviewMessage}>
                        Clear
                      </button>
                    ) : null}
                  </div>
                  {adminDetailState.loading ? (
                    <div className="alert"><span>Loading interaction detail...</span></div>
                  ) : null}
                  {adminDetailState.error ? (
                    <div className="alert alert-error"><span>{adminDetailState.error}</span></div>
                  ) : null}
                  {!adminDetailState.loading && !adminDetailState.error && !detail ? (
                    <p className="text-sm opacity-70">Select an interaction to inspect the question, rewrite, answer, sources, rating, and note.</p>
                  ) : null}
                  {!adminDetailState.loading && !adminDetailState.error && detail ? (
                    <div className="space-y-4">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="badge badge-outline">{detail.user_id}</span>
                        <span className={`badge ${ratingBadgeClass(detail.rating)}`}>{formatRatingLabel(detail.rating)}</span>
                        <span className="opacity-70">Asked {formatMessageDateTime(detail.asked_at || "")}</span>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-1">Question</div>
                        <pre className="debug-pre">{detail.question || "-"}</pre>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-1">Effective Query</div>
                        <pre className="debug-pre">{detail.query_effective || "-"}</pre>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-1">Rewritten Query</div>
                        <pre className="debug-pre">{detail.query_rewritten || "-"}</pre>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-1">LLM Answer</div>
                        <pre className="debug-pre">{detail.answer || "-"}</pre>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-1">Comment</div>
                        <pre className="debug-pre">{detail.note || "-"}</pre>
                      </div>
                      <div>
                        <div className="text-sm font-medium mb-2">Sources Used</div>
                        <div className="flex flex-wrap gap-2">
                          {(detail.sources || []).map((source, idx) => {
                            const normalized = normalizeMessageSources([source])[0];
                            if (!normalized) {
                              return (
                                <span key={`source-${idx}`} className="badge badge-outline">
                                  {source.url || source.url_canonical || `Source ${idx + 1}`}
                                </span>
                              );
                            }
                            return (
                              <a
                                key={`${normalized.canonical_key}-${idx}`}
                                href={normalized.link}
                                target="_blank"
                                rel="noreferrer"
                                className={`badge gap-1 py-3 px-2 text-xs leading-tight ${sourceIsProcedure(normalized)
                                    ? "bg-amber-100/45 border-black/60 text-base-content"
                                    : "badge-outline"
                                  }`}
                                title={sourceHoverTitle(normalized)}
                              >
                                <span className="font-medium">{normalized.label}</span>
                              </a>
                            );
                          })}
                          {(!detail.sources || detail.sources.length === 0) ? (
                            <span className="text-sm opacity-70">No sources recorded.</span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <button className="btn btn-sm" type="button" onClick={() => openDebugPage(detail.assistant_message_id)}>
                          Open debug
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (viewMode === "admin-sources" && canViewAdmin) {
    const proposals = sourceProposalState.proposals || [];
    const selected = sourceProposalState.selected || null;
    const urls = sourceProposalState.urls || [];
    const liveResult = sourceTestResult?.live || null;
    const sandboxResult = sourceTestResult?.sandbox || null;

    const renderResultSources = (sources) => {
      const normalized = normalizeMessageSources(sources || []);
      if (!normalized.length) {
        return <span className="text-sm opacity-70">No sources returned.</span>;
      }
      return (
        <div className="flex flex-wrap gap-2">
          {normalized.map((source, idx) => (
            <a
              key={`${source.canonical_key}-${idx}`}
              href={source.link}
              target="_blank"
              rel="noreferrer"
              className="badge badge-outline gap-1 py-3 px-2 text-xs leading-tight"
              title={sourceHoverTitle(source)}
            >
              {source.label}
            </a>
          ))}
        </div>
      );
    };

    return (
      <div className="min-h-screen bg-base-200">
        {renderPrimaryHeader("Source Test Sets")}
        <div className="p-4 md:p-6">
          <div className="mx-auto max-w-7xl space-y-4">
            <div className="card bg-base-100 border border-base-300 shadow-sm">
              <div className="card-body gap-4">
                <div className="flex flex-wrap items-end gap-3">
                  <label className="form-control grow min-w-72">
                    <span className="label-text text-sm">New test set name</span>
                    <input
                      className="input input-bordered"
                      value={sourceProposalName}
                      onChange={(event) => setSourceProposalName(event.target.value)}
                      placeholder="Source test set"
                    />
                  </label>
                  <button className="btn btn-primary" type="button" onClick={createSourceProposal} disabled={sourceProposalBusy}>
                    Create source test set
                  </button>
                  <button className="btn" type="button" onClick={loadSourceProposals} disabled={sourceProposalState.loading}>
                    Refresh list
                  </button>
                </div>
                <p className="text-sm opacity-70">
                  Each test set copies the live scraper database into an isolated sandbox. URL edits, refreshes, and test queries here do not change live answers until you promote.
                </p>
              </div>
            </div>

            {sourceProposalState.loading ? <div className="alert"><span>Loading source test sets...</span></div> : null}
            {sourceProposalState.error ? <div className="alert alert-error"><span>{sourceProposalState.error}</span></div> : null}

            <div className="grid gap-4 xl:grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)]">
              <div className="card bg-base-100 border border-base-300 shadow-sm">
                <div className="card-body">
                  <h2 className="card-title text-base">Test sets</h2>
                  <div className="space-y-2">
                    {proposals.map((proposal) => (
                      <button
                        key={proposal.id}
                        className={`btn btn-sm w-full justify-start ${selected?.id === proposal.id ? "btn-primary" : "btn-ghost"}`}
                        type="button"
                        onClick={() => loadSourceProposalDetail(proposal.id)}
                      >
                        <span className="truncate">#{proposal.id} {proposal.name}</span>
                        <span className="badge badge-sm ml-auto">{proposal.status}</span>
                      </button>
                    ))}
                    {!sourceProposalState.loading && proposals.length === 0 ? (
                      <p className="text-sm opacity-70">No source test sets yet.</p>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                <div className="card bg-base-100 border border-base-300 shadow-sm">
                  <div className="card-body gap-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <h2 className="card-title text-base">{selected ? selected.name : "Select a test set"}</h2>
                        {selected ? (
                          <div className="mt-1 flex flex-wrap gap-2 text-sm">
                            <span className="badge badge-outline">#{selected.id}</span>
                            <span className="badge">{selected.status}</span>
                            <span className="opacity-70">URLs changed: {selected.url_count || urls.length || 0}</span>
                          </div>
                        ) : null}
                      </div>
                      {selected ? (
                        <div className="flex flex-wrap gap-2">
                          <button className="btn btn-sm" type="button" onClick={refreshSourceProposal} disabled={sourceProposalBusy}>
                            Queue sandbox refresh
                          </button>
                          <button className="btn btn-sm btn-success" type="button" onClick={promoteSourceProposal} disabled={sourceProposalBusy || !urls.length || selected.status === "promoted"}>
                            Promote URL changes
                          </button>
                        </div>
                      ) : null}
                    </div>
                    {selected?.last_refresh_finished_at ? (
                      <p className="text-sm opacity-70">Last sandbox refresh: {formatMessageDateTime(selected.last_refresh_finished_at)}</p>
                    ) : null}
                    {selected?.error ? <div className="alert alert-error"><span>{selected.error}</span></div> : null}
                  </div>
                </div>

                {selected ? (
                  <>
                    <div className="card bg-base-100 border border-base-300 shadow-sm">
                      <div className="card-body gap-4">
                        <h2 className="card-title text-base">Add or remove sandbox source URL</h2>
                        <div className="flex flex-wrap items-end gap-3">
                          <label className="form-control">
                            <span className="label-text text-sm">Action</span>
                            <select className="select select-bordered" value={sourceProposalUrlAction} onChange={(event) => setSourceProposalUrlAction(event.target.value)}>
                              <option value="add">Add</option>
                              <option value="remove">Remove</option>
                            </select>
                          </label>
                          <label className="form-control grow min-w-72">
                            <span className="label-text text-sm">URL</span>
                            <input
                              className="input input-bordered"
                              value={sourceProposalUrl}
                              onChange={(event) => setSourceProposalUrl(event.target.value)}
                              placeholder="https://connections/?docs=..."
                            />
                          </label>
                          <button className="btn btn-primary" type="button" onClick={submitSourceProposalUrl} disabled={sourceProposalBusy || !sourceProposalUrl.trim()}>
                            Save URL change
                          </button>
                        </div>
                        <div className="overflow-x-auto">
                          <table className="table table-sm">
                            <thead><tr><th>Action</th><th>URL</th><th>When</th><th>By</th></tr></thead>
                            <tbody>
                              {urls.map((row) => (
                                <tr key={row.id}>
                                  <td><span className={`badge ${row.action === "remove" ? "badge-error" : "badge-success"}`}>{row.action}</span></td>
                                  <td className="max-w-xl whitespace-normal break-all">{row.url}</td>
                                  <td>{formatMessageDateTime(row.created_at || "")}</td>
                                  <td>{row.created_by || "-"}</td>
                                </tr>
                              ))}
                              {urls.length === 0 ? <tr><td colSpan={4} className="opacity-70">No URL changes recorded.</td></tr> : null}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </div>

                    <div className="card bg-base-100 border border-base-300 shadow-sm">
                      <div className="card-body gap-4">
                        <h2 className="card-title text-base">Compare a test query</h2>
                        <div className="flex flex-wrap items-end gap-3">
                          <label className="form-control grow min-w-72">
                            <span className="label-text text-sm">Question</span>
                            <input
                              className="input input-bordered"
                              value={sourceTestQuery}
                              onChange={(event) => setSourceTestQuery(event.target.value)}
                              placeholder="Ask a Connections question to compare live vs sandbox"
                            />
                          </label>
                          <button className="btn btn-primary" type="button" onClick={runSourceTestQuery} disabled={sourceProposalBusy || !sourceTestQuery.trim()}>
                            Run comparison
                          </button>
                        </div>
                        {sourceProposalBusy ? <div className="text-sm opacity-70">Working...</div> : null}
                        {sourceTestResult ? (
                          <div className="grid gap-4 lg:grid-cols-2">
                            <div className="rounded-box border border-base-300 p-3">
                              <h3 className="font-semibold mb-2">Live answer</h3>
                              <pre className="debug-pre min-h-32">{liveResult?.answer || "-"}</pre>
                              <div className="mt-3"><div className="text-sm font-medium mb-2">Sources</div>{renderResultSources(liveResult?.sources || [])}</div>
                            </div>
                            <div className="rounded-box border border-base-300 p-3">
                              <h3 className="font-semibold mb-2">Sandbox answer</h3>
                              <pre className="debug-pre min-h-32">{sandboxResult?.answer || "-"}</pre>
                              <div className="mt-3"><div className="text-sm font-medium mb-2">Sources</div>{renderResultSources(sandboxResult?.sources || [])}</div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
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
                    Back
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
            {canViewAdmin ? (
              <>
                <button className="btn btn-ghost btn-sm" type="button" onClick={() => openView("admin-stats")}>
                  Admin stats
                </button>
                <button className="btn btn-ghost btn-sm" type="button" onClick={() => openView("admin-review")}>
                  Interaction review
                </button>
              </>
            ) : null}
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
              <div className="flex items-center gap-2">
                <div className="avatar placeholder">
                  <div
                    className="h-9 min-w-16 rounded-full px-2 text-white flex items-center justify-center leading-none"
                    style={{ backgroundColor: profile.avatar.color }}
                  >
                    <span className="text-[10px] font-semibold tracking-tight">
                      {profile.avatar.initials}
                    </span>
                  </div>
                </div>
                <button className="btn btn-ghost btn-sm" type="button" onClick={clearLoginState}>
                  Switch user
                </button>
              </div>
            ) : null}
            <ThemeDropdown />
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
                    const negativeFeedbackOpen = activeNegativeFeedbackMessageId === msg.id;
                    const negativeFeedbackDraft = String(
                      negativeFeedbackDraftByMessage?.[msg.id] || ""
                    );
                    const negativeFeedbackPending = Boolean(
                      negativeFeedbackPendingByMessage?.[msg.id]
                    );
                    const negativeFeedbackSubmitting = Boolean(
                      submittingNegativeByMessage?.[msg.id]
                    );
                    const negativeFeedbackError = String(
                      negativeFeedbackErrorByMessage?.[msg.id] || ""
                    );
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
                                    className={`badge gap-1 py-3 px-2 text-xs leading-tight ${sourceIsProcedure(source)
                                        ? "bg-amber-100/45 border-black/60 text-base-content"
                                        : "badge-outline"
                                      }`}
                                    title={`${sourceHoverTitle(source)}\nClick to preview in-app. Ctrl/Cmd-click opens a new tab.`}
                                    onClick={(event) => {
                                      if (
                                        event.metaKey ||
                                        event.ctrlKey ||
                                        event.shiftKey ||
                                        event.altKey
                                      ) {
                                        return;
                                      }
                                      event.preventDefault();
                                      openSourcePreview(source);
                                    }}
                                  >
                                    <span
                                      className={`font-medium ${linkedSourceKeySet.has(source.canonical_key) ? "underline" : ""
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
                                onClick={() => submitPositiveFeedback(msg.id)}
                                type="button"
                                aria-pressed={upSelected}
                                aria-label="Mark response helpful"
                              >
                                <ThumbsUp className={`size-[1.2em] ${upSelected ? "fill-current" : ""}`} />
                              </button>
                              <button
                                className={`btn btn-square btn-ghost ${downSelected ? "bg-error/15 text-error" : ""}`}
                                onClick={() => toggleNegativeFeedback(msg.id)}
                                type="button"
                                aria-pressed={downSelected}
                                aria-label="Mark response unhelpful"
                              >
                                <ThumbsDown className={`size-[1.2em] ${downSelected ? "fill-current" : ""}`} />
                              </button>
                              <button
                                className="btn btn-outline btn-sm"
                                onClick={() => explainToCustomer(msg.id)}
                                type="button"
                                disabled={sending || !String(msg.content || "").trim()}
                                title="Ask the assistant to turn this interaction into a customer-friendly explanation"
                              >
                                Explain to Customer
                              </button>
                            </div>
                            {negativeFeedbackOpen && negativeFeedbackPending ? (
                              <div className="mt-3 rounded-box border border-base-300 bg-base-100 p-3">
                                <label className="form-control gap-2">
                                  <span className="label-text text-sm font-medium">
                                    Tell us what went wrong
                                  </span>
                                  <textarea
                                    className="textarea textarea-bordered min-h-24 w-full"
                                    placeholder="Optional details about why this response was not helpful"
                                    value={negativeFeedbackDraft}
                                    onChange={(event) =>
                                      setNegativeFeedbackDraftByMessage((prev) => ({
                                        ...prev,
                                        [msg.id]: event.target.value,
                                      }))
                                    }
                                    disabled={negativeFeedbackSubmitting}
                                  />
                                </label>
                                {negativeFeedbackError ? (
                                  <p className="mt-2 text-sm text-error">{negativeFeedbackError}</p>
                                ) : null}
                                <div className="mt-3 flex flex-wrap gap-2">
                                  <button
                                    className="btn btn-sm btn-error"
                                    type="button"
                                    onClick={() => handleNegativeFeedbackSubmit(msg.id)}
                                    disabled={negativeFeedbackSubmitting}
                                  >
                                    {negativeFeedbackSubmitting ? "Submitting..." : "Submit"}
                                  </button>
                                  <button
                                    className="btn btn-sm"
                                    type="button"
                                    onClick={() => handleNegativeFeedbackSkip(msg.id)}
                                    disabled={negativeFeedbackSubmitting}
                                  >
                                    Skip
                                  </button>
                                </div>
                              </div>
                            ) : null}
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
        <div className="flex min-h-full max-w-[calc(100vw-1rem)] flex-col items-start bg-base-200 is-drawer-close:w-14 is-drawer-open:w-80">
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
              const sidebarPreview = formatSidebarChatPreview(title);
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
                      <span className="is-drawer-open truncate">{sidebarPreview}</span>
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

      {sourcePreviewState.open && (
        <dialog className="modal modal-open">
          <div className="modal-box max-w-7xl w-11/12 h-[88vh] p-0 overflow-hidden">
            <div className="flex items-start justify-between gap-3 border-b border-base-300 bg-base-100 p-3">
              <div className="min-w-0">
                <h3 className="font-semibold truncate">
                  {sourcePreviewState.title || "Source preview"}
                </h3>
                <p className="text-xs opacity-70 truncate">{sourcePreviewState.url}</p>
                <p className="text-xs opacity-60">
                  Last scraped: {formatLastScraped(sourcePreviewState.lastScraped || "")}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <a
                  className="btn btn-sm"
                  href={sourcePreviewState.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open in new tab
                </a>
                <button className="btn btn-sm btn-ghost" type="button" onClick={closeSourcePreview}>
                  Close
                </button>
              </div>
            </div>
            <div className="h-[calc(88vh-6.25rem)] bg-base-200">
              {sourcePreviewState.loading ? (
                <div className="h-full flex items-center justify-center text-sm opacity-70">
                  Loading source preview...
                </div>
              ) : sourcePreviewState.error ? (
                <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center">
                  <div className="text-error">{sourcePreviewState.error}</div>
                  <a
                    className="btn btn-sm"
                    href={sourcePreviewState.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open source in new tab
                  </a>
                </div>
              ) : (
                <iframe
                  title={sourcePreviewState.title || "Source preview"}
                  className="h-full w-full bg-white"
                  srcDoc={sourcePreviewState.html}
                  sandbox="allow-popups allow-popups-to-escape-sandbox"
                />
              )}
            </div>
          </div>
          <form method="dialog" className="modal-backdrop">
            <button type="button" onClick={closeSourcePreview}>
              close
            </button>
          </form>
        </dialog>
      )}

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
