// @ts-nocheck
import React, { useEffect } from "react";
import type { Meta, StoryObj } from "@storybook/react";

import { ChatApp } from "./ChatApp";

function toJsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function toSseResponse(events) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const event of events) {
        const chunk = `event: ${event.type}\ndata: ${JSON.stringify(event.payload)}\n\n`;
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function createMockFetch(mode = "default") {
  const defaultSources = [
    {
      url: "https://connections/?docs=residential%2Fbilling-payments-refunds%2Fpayment-arrangement-b%2Fbroken-payment-arrangement",
      last_scraped: "2026-01-27T15:37:14.208027",
      has_tab_steps: true,
      tab_step_count: 2,
      vector_score_raw: 0.72,
      bm25_score_raw: 5.2,
      source_score_eligible: true,
      procedure_link_eligible: true,
    },
    {
      url: "https://connections/?docs=residential/billing-payments-refunds/payment-arrangement-b/re-working-a-payment-arrangement",
      last_scraped: "2026-01-27T15:18:01.337120",
      has_tab_steps: false,
      tab_step_count: 0,
      vector_score_raw: 0.69,
      bm25_score_raw: 2.0,
      source_score_eligible: true,
      procedure_link_eligible: false,
    },
    {
      url: "https://connections/?docs=residential/billing-payments-refunds/payment-arrangement-b/broken-payment-arrangement",
      last_scraped: "2026-01-27T15:37:14.208027",
      has_tab_steps: true,
      tab_step_count: 1,
      vector_score_raw: 0.67,
      bm25_score_raw: 3.4,
      source_score_eligible: true,
      procedure_link_eligible: true,
    },
  ];
  const seedAssistantSources = mode === "empty-sources" ? [] : defaultSources;
  const streamSources = mode === "empty-sources" ? [] : defaultSources.slice(0, 2);
  const storyVersion = "0.1.10";
  const assistantSeedContent =
    mode === "markdown-links"
      ? `Use [Broken arrangement](${defaultSources[0].url}), [Re-work arrangement](${defaultSources[1].url}), and [Broken arrangement copy](${defaultSources[2].url}). Ignore [non-source link](https://example.com/not-a-source).`
      : "Try hybrid retrieval with score normalization, then tune reranking and chunk size.";
  const streamAnswer =
    mode === "markdown-links"
      ? `Storybook mock answer: [primary source](${defaultSources[0].url}), [secondary source](${defaultSources[1].url}), and [external](https://example.com/external).`
      : null;
  const chatOneMessages =
    mode === "source-collapse"
      ? [
          {
            id: 1001,
            role: "user",
            content: "How can we improve retrieval quality?",
            sources: [],
            created_at: "2026-02-23T09:01:00Z",
            question_created_at: "2026-02-23T09:01:00Z",
            app_version: storyVersion,
          },
          {
            id: 1002,
            role: "assistant",
            content: assistantSeedContent,
            sources: seedAssistantSources,
            has_debug: true,
            created_at: "2026-02-23T09:01:03Z",
            question_created_at: "2026-02-23T09:01:00Z",
            app_version: storyVersion,
          },
          {
            id: 1003,
            role: "user",
            content: "What should we do next for query rewrite and source quality?",
            sources: [],
            created_at: "2026-02-23T09:02:00Z",
            question_created_at: "2026-02-23T09:02:00Z",
            app_version: storyVersion,
          },
          {
            id: 1004,
            role: "assistant",
            content:
              "Use dual retrieval pass analysis and monitor overlap per turn to confirm source freshness.",
            sources: streamSources,
            has_debug: true,
            created_at: "2026-02-23T09:02:04Z",
            question_created_at: "2026-02-23T09:02:00Z",
            app_version: storyVersion,
          },
        ]
      : [
          {
            id: 1001,
            role: "user",
            content: "How can we improve retrieval quality?",
            sources: [],
            created_at: "2026-02-23T09:01:00Z",
            question_created_at: "2026-02-23T09:01:00Z",
            app_version: storyVersion,
          },
          {
            id: 1002,
            role: "assistant",
            content: assistantSeedContent,
            sources: seedAssistantSources,
            has_debug: true,
            created_at: "2026-02-23T09:01:03Z",
            question_created_at: "2026-02-23T09:01:00Z",
            app_version: storyVersion,
          },
        ];

  const state = {
    chats: [
      { id: 1, title: "RAG tuning notes" },
      { id: 2, title: "Weekly ops review" },
    ],
    messagesByChat: {
      1: chatOneMessages,
      2: [],
    },
    profiles: [
      { ip: "10.0.0.8", avatar: { initials: "A8", color: "#2563eb" } },
      { ip: "10.0.0.19", avatar: { initials: "B9", color: "#16a34a" } },
    ],
    profile: {
      ip: "192.168.1.24",
      avatar: { initials: "H24", color: "#ea580c" },
    },
    nextChatId: 3,
    nextMessageId: 3000,
  };

  return async function mockFetch(input, init = {}) {
    const method = (init.method || "GET").toUpperCase();
    const requestUrl = typeof input === "string" ? input : input.url;
    const url = new URL(requestUrl, window.location.origin);
    const path = url.pathname;

    if (!path.startsWith("/api/")) {
      return new Response("Not found", { status: 404 });
    }

    if (path === "/api/chats" && method === "GET") {
      return toJsonResponse({ chats: state.chats });
    }

    if (path === "/api/chats" && method === "POST") {
      const chat = { id: state.nextChatId, title: `Chat ${state.nextChatId}` };
      state.nextChatId += 1;
      state.chats = [chat, ...state.chats];
      state.messagesByChat[chat.id] = [];
      return toJsonResponse({ chat }, 201);
    }

    const chatsMatch = path.match(/^\/api\/chats\/(\d+)$/);
    if (chatsMatch && method === "PATCH") {
      const chatId = Number.parseInt(chatsMatch[1], 10);
      const body = init.body ? JSON.parse(String(init.body)) : {};
      state.chats = state.chats.map((chat) =>
        chat.id === chatId ? { ...chat, title: body.title || chat.title } : chat
      );
      return toJsonResponse({ ok: true });
    }

    if (chatsMatch && method === "DELETE") {
      const chatId = Number.parseInt(chatsMatch[1], 10);
      state.chats = state.chats.filter((chat) => chat.id !== chatId);
      delete state.messagesByChat[chatId];
      return toJsonResponse({ ok: true });
    }

    const messagesMatch = path.match(/^\/api\/chats\/(\d+)\/messages$/);
    if (messagesMatch && method === "GET") {
      const chatId = Number.parseInt(messagesMatch[1], 10);
      return toJsonResponse({ messages: state.messagesByChat[chatId] || [] });
    }

    if (messagesMatch && method === "POST") {
      const chatId = Number.parseInt(messagesMatch[1], 10);
      const body = init.body ? JSON.parse(String(init.body)) : {};
      const userMessageId = state.nextMessageId++;
      const assistantMessageId = state.nextMessageId++;
      const message = body.message || "";
      const nowIso = new Date().toISOString();

      const userMessage = {
        id: userMessageId,
        role: "user",
        content: message,
        sources: [],
        created_at: nowIso,
        question_created_at: nowIso,
        app_version: storyVersion,
      };
      const assistantMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        sources: [],
        has_debug: true,
        created_at: nowIso,
        question_created_at: nowIso,
        app_version: storyVersion,
      };

      if (!state.messagesByChat[chatId]) {
        state.messagesByChat[chatId] = [];
      }
      state.messagesByChat[chatId].push(userMessage, assistantMessage);
      return toJsonResponse(
        {
          user_message_id: userMessageId,
          assistant_message_id: assistantMessageId,
          stream_id: `stream-${assistantMessageId}`,
          user_created_at: nowIso,
          assistant_created_at: nowIso,
          question_created_at: nowIso,
          app_version: storyVersion,
        },
        201
      );
    }

    if (path === "/api/stream" && method === "POST") {
      const params = new URLSearchParams(String(init.body || ""));
      const prompt = params.get("message") || "";
      const answer = streamAnswer || `Storybook mock answer: ${prompt}`;
      return toSseResponse([
        { type: "delta", payload: { text: answer } },
        {
          type: "sources",
          payload: {
            sources: streamSources,
          },
        },
        { type: "debug", payload: { ready: true } },
        { type: "done", payload: {} },
      ]);
    }

    if (path === "/api/feedback" && method === "POST") {
      return toJsonResponse({ ok: true }, 201);
    }

    const debugMatch = path.match(/^\/api\/messages\/(\d+)\/debug$/);
    if (debugMatch && method === "GET") {
      const messageId = Number.parseInt(debugMatch[1], 10);
      if (mode === "error") {
        return toJsonResponse({ detail: "Debug payload unavailable" }, 500);
      }

      return toJsonResponse({
        debug: {
          query: "How can we improve retrieval quality?",
          query_effective:
            "How can we improve retrieval quality for our pipeline using the prior conversation context?",
          query_rewritten:
            "Based on our prior discussion on retrieval failures, how can we improve retrieval quality for our pipeline?",
          query_rewrite: {
            used: true,
            reason: "used",
            model: "gpt-5-nano",
            history_turns: 4,
          },
          retrieval: {
            candidate_counts: { vector: 8, bm25: 8, merged: 12 },
            ranked_chunks: [
              {
                rank: 1,
                score: 0.9321,
                from_vector: true,
                from_bm25: true,
                vector_score_norm: 0.92,
                bm25_score_norm: 0.94,
                chunk_id: 42,
                url: "https://example.com/docs/hybrid-search",
              },
            ],
          },
          sources: [
            {
              extract_id: 42,
              score: 0.9321,
              from_vector: true,
              from_bm25: true,
              vector_score_raw: 0.88,
              bm25_score_raw: 8.4,
              url: "https://example.com/docs/hybrid-search",
              last_scraped: "2026-01-27T15:37:14.208027",
              has_tab_steps: true,
              tab_step_count: 2,
              source_score_eligible: true,
              procedure_link_eligible: true,
            },
          ],
          llm_request: {
            system_text: "You are a helpful assistant.",
            user_text: "Answer based only on provided sources.",
          },
          llm_response_text: `Debug payload for message ${messageId}.`,
        },
      });
    }

    if (path === "/api/profile" && method === "GET") {
      return toJsonResponse(state.profile);
    }

    if (path === "/api/release" && method === "GET") {
      return toJsonResponse({
        version: storyVersion,
        changelog_url: "/connections/reference/changelog",
        last_crawled: "2026-02-23T03:15:00Z",
      });
    }

    if (path === "/api/profiles" && method === "GET") {
      return toJsonResponse({ profiles: state.profiles });
    }

    if (path === "/api/profiles" && method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) : {};
      const profile = {
        ip: body.ip,
        avatar: { initials: "NP", color: "#9333ea" },
      };
      state.profiles = [...state.profiles, profile];
      return toJsonResponse(profile, 201);
    }

    return new Response("Not found", { status: 404 });
  };
}

function applyStoryUrl(mode) {
  const url = new URL(window.location.href);
  url.searchParams.delete("chat_id");
  url.searchParams.delete("debug_message_id");
  if (mode === "error") {
    url.searchParams.set("debug_message_id", "1002");
  }
  window.history.replaceState({}, "", url);
}

function MockAppEnvironment({ children, mode = "default" }) {
  useEffect(() => {
    const originalFetch = window.fetch.bind(window);
    const originalHref = window.location.href;
    const mockFetch = createMockFetch(mode);

    window.fetch = (input, init) => mockFetch(input, init);

    return () => {
      window.fetch = originalFetch;
      window.history.replaceState({}, "", originalHref);
    };
  }, [mode]);

  return children;
}

const meta: Meta<typeof ChatApp> = {
  title: "Components/ChatApp",
  component: ChatApp,
  parameters: {
    layout: "fullscreen",
  },
  decorators: [
    (Story) => {
      applyStoryUrl("default");
      return (
        <MockAppEnvironment mode="default">
          <Story />
        </MockAppEnvironment>
      );
    },
  ],
};

export default meta;

type Story = StoryObj<typeof ChatApp>;

export const Default: Story = {};

export const LoadingError: Story = {
  name: "Loading/Error",
  decorators: [
    (Story) => {
      applyStoryUrl("error");
      return (
        <MockAppEnvironment mode="error">
          <Story />
        </MockAppEnvironment>
      );
    },
  ],
};

export const EmptySources: Story = {
  name: "Empty Sources",
  decorators: [
    (Story) => {
      applyStoryUrl("default");
      return (
        <MockAppEnvironment mode="empty-sources">
          <Story />
        </MockAppEnvironment>
      );
    },
  ],
};

export const SourceCollapseDefaults: Story = {
  name: "Source Collapse Defaults",
  decorators: [
    (Story) => {
      applyStoryUrl("default");
      return (
        <MockAppEnvironment mode="source-collapse">
          <Story />
        </MockAppEnvironment>
      );
    },
  ],
};

export const MarkdownSourceLinks: Story = {
  name: "Markdown Source Links",
  decorators: [
    (Story) => {
      applyStoryUrl("default");
      return (
        <MockAppEnvironment mode="markdown-links">
          <Story />
        </MockAppEnvironment>
      );
    },
  ],
};
