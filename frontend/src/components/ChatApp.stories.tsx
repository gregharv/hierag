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
  const state = {
    chats: [
      { id: 1, title: "RAG tuning notes" },
      { id: 2, title: "Weekly ops review" },
    ],
    messagesByChat: {
      1: [
        {
          id: 1001,
          role: "user",
          content: "How can we improve retrieval quality?",
          sources: [],
        },
        {
          id: 1002,
          role: "assistant",
          content:
            "Try hybrid retrieval with score normalization, then tune reranking and chunk size.",
          sources: [
            { url: "https://example.com/docs/hybrid-search" },
            { url: "https://example.com/docs/chunking" },
          ],
          has_debug: true,
        },
      ],
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

      const userMessage = {
        id: userMessageId,
        role: "user",
        content: message,
        sources: [],
      };
      const assistantMessage = {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        sources: [],
        has_debug: true,
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
        },
        201
      );
    }

    if (path === "/api/stream" && method === "POST") {
      const params = new URLSearchParams(String(init.body || ""));
      const prompt = params.get("message") || "";
      const answer = `Storybook mock answer: ${prompt}`;
      return toSseResponse([
        { type: "delta", payload: { text: answer } },
        {
          type: "sources",
          payload: { sources: [{ url: "https://example.com/mock-source" }] },
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
