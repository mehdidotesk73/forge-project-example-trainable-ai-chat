/**
 * AiChat — drop-in AI chat component for Forge projects.
 *
 * Endpoint UUIDs are hard-coded here — they must stay in sync with the values
 * in endpoint_repos/ai_chat_endpoints/ai_chat_endpoints/endpoints.py
 *
 * Host usage:
 *   import { AiChat } from "@forge-suite/ai-chat";
 *   <AiChat />
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ButtonGroup,
  Container,
  LogPanel,
  Navbar,
  Selector,
  TextArea,
  callEndpoint,
  callStreamingEndpoint,
} from "@forge-suite/ts";
import type { LogLine } from "@forge-suite/ts";

// ── Permanent endpoint UUIDs — must match endpoints.py ───────────────────────

const EP_START_SESSION = "a1ca4000-0002-0000-0000-000000000001";
const EP_SEND_MESSAGE = "a1ca4000-0002-0000-0000-000000000002";
const EP_UPLOAD_CONTEXT = "a1ca4000-0002-0000-0000-000000000003";
const EP_LIST_SESSIONS = "a1ca4000-0002-0000-0000-000000000005";
const EP_DELETE_SESSION = "a1ca4000-0002-0000-0000-000000000006";
const EP_LIST_MODELS = "a1ca4000-0002-0000-0000-000000000007";
const EP_SAVE_CONFIG = "a1ca4000-0002-0000-0000-000000000009";
const EP_UPDATE_SESSION = "a1ca4000-0002-0000-0000-000000000010";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Session {
  session_id: string;
  title: string;
  mode: string;
  model_id: string;
  updated_at?: string;
  created_at: string;
}

interface Message {
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

interface ModelOption {
  id: string;
  name: string;
  provider: string;
}

export interface AiChatProps {
  /** Resume a specific session by ID on mount. */
  initialSessionId?: string;
  /** Lock the mode — if omitted the user can toggle. */
  mode?: "train" | "ask";
}

// ── Helper ────────────────────────────────────────────────────────────────────

function ts(): number {
  return Date.now();
}

function messageToLogLines(messages: Message[]): LogLine[] {
  return messages.flatMap((m) => {
    if (m.role === "system") return [];
    const prefix = m.role === "user" ? "You" : "AI";
    // Split multi-line assistant content into separate log lines
    return m.content.split("\n").map((line, i) => ({
      event: m.role === "user" ? "status" : "message",
      data: i === 0 ? `${prefix}: ${line}` : `       ${line}`,
      ts: new Date(m.created_at).getTime(),
    }));
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

export function AiChat({ initialSessionId, mode: forcedMode }: AiChatProps) {
  // ── Core state ─────────────────────────────────────────────────────────────
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [logLines, setLogLines] = useState<LogLine[]>([]);
  const [inputText, setInputText] = useState("");
  const [streaming, setStreaming] = useState(false);

  // ── Setup controls ─────────────────────────────────────────────────────────
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState(
    "claude-3-5-sonnet-20241022",
  );
  const [selectedMode, setSelectedMode] = useState<"train" | "ask">(
    forcedMode ?? "train",
  );
  const [newTitle, setNewTitle] = useState("New Chat");

  // ── Context upload state ───────────────────────────────────────────────────
  const [contextText, setContextText] = useState("");
  const [showContextPanel, setShowContextPanel] = useState(false);

  // ── API key prompt state ───────────────────────────────────────────────────
  const [pendingApiKeyProvider, setPendingApiKeyProvider] = useState<
    string | null
  >(null);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const pendingMessageRef = useRef<string>("");

  // ── In-session selector guard (refs — no re-render, no stale closure) ──────
  // lastSentModelRef / lastSentModeRef track the value last dispatched to the
  // API so rapid onChange fires with the same value are no-ops.
  const lastSentModelRef = useRef<string>("");
  const lastSentModeRef = useRef<string>("");

  // Used to accumulate streaming tokens into a single assistant message line
  const streamBufferRef = useRef<string>("");
  const streamLineIndexRef = useRef<number>(-1);

  // ── Load models + sessions on mount ───────────────────────────────────────
  useEffect(() => {
    callEndpoint<ModelOption[]>(EP_LIST_MODELS, {}).then((models) => {
      setAvailableModels(models);
    });
    callEndpoint<Session[]>(EP_LIST_SESSIONS, {}).then((list) => {
      setSessions(list);
    });

    if (initialSessionId) {
      handleResumeSession(initialSessionId);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Session management ─────────────────────────────────────────────────────

  const appendLog = useCallback((event: string, data: string) => {
    setLogLines((prev) => [...prev, { event, data, ts: ts() }]);
  }, []);

  const handleNewSession = async () => {
    try {
      const result = await callEndpoint<{ session_id: string } & Session>(
        EP_START_SESSION,
        {
          title: newTitle,
          mode: forcedMode ?? selectedMode,
          model_id: selectedModel,
        },
      );
      const session: Session = {
        session_id: result.session_id,
        title: result.title,
        mode: result.mode,
        model_id: result.model_id,
        created_at: result.created_at,
      };
      setActiveSession(session);
      setLogLines([]);
      streamBufferRef.current = "";
      streamLineIndexRef.current = -1;
      lastSentModelRef.current = session.model_id;
      lastSentModeRef.current = session.mode;
      setSessions((prev) => [
        session,
        ...prev.filter((s) => s.session_id !== session.session_id),
      ]);
      appendLog(
        "status",
        `Session started: ${session.title} [${session.mode} / ${session.model_id}]`,
      );
    } catch (e) {
      appendLog("error", `Failed to start session: ${e}`);
    }
  };

  const handleResumeSession = async (sessionId: string) => {
    try {
      const result = await callEndpoint<Session & { messages: Message[] }>(
        EP_START_SESSION,
        { session_id: sessionId },
      );
      const session: Session = {
        session_id: result.session_id,
        title: result.title,
        mode: result.mode,
        model_id: result.model_id,
        created_at: result.created_at,
      };
      setActiveSession(session);
      setLogLines(messageToLogLines(result.messages ?? []));
      streamBufferRef.current = "";
      streamLineIndexRef.current = -1;
      lastSentModelRef.current = session.model_id;
      lastSentModeRef.current = session.mode;
    } catch (e) {
      appendLog("error", `Failed to resume session: ${e}`);
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    await callEndpoint(EP_DELETE_SESSION, { session_id: sessionId });
    setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
    if (activeSession?.session_id === sessionId) {
      setActiveSession(null);
      setLogLines([]);
    }
  };

  const handleUpdateSession = async (
    field: "mode" | "model_id",
    value: string,
  ) => {
    if (!activeSession || streaming) return;
    // Guard via refs — synchronous, never stale, no re-render side-effects.
    // Prevents duplicate API calls from Selector firing onChange on remount.
    const lastSentRef =
      field === "model_id" ? lastSentModelRef : lastSentModeRef;
    if (lastSentRef.current === value) return;
    lastSentRef.current = value; // lock immediately before async call
    const result = await callEndpoint<
      Session & { messages: Message[]; log_message?: string }
    >(EP_UPDATE_SESSION, {
      session_id: activeSession.session_id,
      [field]: value,
    });
    const updated: Session = {
      session_id: result.session_id,
      title: result.title,
      mode: result.mode,
      model_id: result.model_id,
      created_at: result.created_at,
    };
    setActiveSession(updated);
    lastSentModelRef.current = updated.model_id;
    lastSentModeRef.current = updated.mode;
    setSessions((prev) =>
      prev.map((s) => (s.session_id === updated.session_id ? updated : s)),
    );
    if (field === "mode") {
      setLogLines(messageToLogLines(result.messages ?? []));
    }
    if (result.log_message) {
      appendLog("status", result.log_message);
    }
  };

  // ── Send message ───────────────────────────────────────────────────────────

  const handleSend = async () => {
    if (!activeSession || !inputText.trim() || streaming) return;
    const message = inputText.trim();
    setInputText("");
    pendingMessageRef.current = message;
    console.log(
      "[handleSend] message:",
      JSON.stringify(message),
      "session_id:",
      activeSession.session_id,
    );
    appendLog("status", `You: ${message}`);
    setStreaming(true);

    // Set the AI line index inside the functional update so `prev` is always
    // the committed state (never stale — this fixes the empty AI: line bug).
    streamBufferRef.current = "";
    setLogLines((prev) => {
      streamLineIndexRef.current = prev.length;
      console.log(
        "[handleSend] AI placeholder at index:",
        prev.length,
        "total lines after:",
        prev.length + 1,
      );
      return [...prev, { event: "message", data: "AI: ", ts: ts() }];
    });

    console.log(
      "[handleSend] calling callStreamingEndpoint, streamLineIndexRef.current before await:",
      streamLineIndexRef.current,
    );

    await callStreamingEndpoint(
      EP_SEND_MESSAGE,
      { session_id: activeSession.session_id, message },
      {
        onEvent(event, data) {
          // Capture the ref synchronously here — React batches setLogLines so
          // by the time the functional update runs, onDone may have reset the
          // ref to -1 already.
          const capturedLineIdx = streamLineIndexRef.current;
          if (event === "token") {
            console.log(
              "[stream token] streamLineIndexRef.current:",
              streamLineIndexRef.current,
              "token length:",
              data.length,
            );
            streamBufferRef.current += data;
            const accumulated = streamBufferRef.current;
            setLogLines((prev) => {
              const next = [...prev];
              const idx = capturedLineIdx;
              console.log(
                "[token setLogLines] prev.length:",
                prev.length,
                "idx:",
                idx,
                "match:",
                idx >= 0 && idx < next.length,
              );
              if (idx >= 0 && idx < next.length) {
                next[idx] = { ...next[idx], data: `AI: ${accumulated}` };
              }
              return next;
            });
          } else if (event === "skill_saved") {
            const [skillName] = data.split(":");
            appendLog("status", `Skill updated: ${skillName}`);
          } else if (event === "api_key_required") {
            setPendingApiKeyProvider(data);
            setLogLines((prev) => {
              const next = [...prev];
              const idx = capturedLineIdx;
              if (idx >= 0 && idx < next.length) {
                next[idx] = {
                  ...next[idx],
                  data: `AI: [${data.toUpperCase()}_API_KEY required — enter key below]`,
                };
              }
              return next;
            });
          } else if (event === "error") {
            setLogLines((prev) => {
              const next = [...prev];
              const idx = capturedLineIdx;
              if (idx >= 0 && idx < next.length) {
                next[idx] = {
                  ...next[idx],
                  event: "error",
                  data: `Error: ${data}`,
                };
              } else {
                next.push({ event: "error", data: `Error: ${data}`, ts: ts() });
              }
              return next;
            });
          }
        },
        onDone() {
          console.log(
            "[stream done] buffer length:",
            streamBufferRef.current.length,
          );
          // If no tokens arrived, mark the placeholder so the line isn't silently empty
          if (!streamBufferRef.current) {
            setLogLines((prev) => {
              const next = [...prev];
              const idx = streamLineIndexRef.current;
              if (idx >= 0 && idx < next.length && next[idx].data === "AI: ") {
                next[idx] = {
                  ...next[idx],
                  event: "error",
                  data: "AI: [no response received]",
                };
              }
              return next;
            });
          }
          setStreaming(false);
          streamBufferRef.current = "";
          streamLineIndexRef.current = -1;
        },
        onError(err) {
          console.error("[stream error]", err);
          // Update the AI placeholder with the error rather than adding a separate line
          setLogLines((prev) => {
            const next = [...prev];
            const idx = streamLineIndexRef.current;
            if (idx >= 0 && idx < next.length) {
              next[idx] = {
                ...next[idx],
                event: "error",
                data: `AI: Error — ${err.message}`,
              };
            } else {
              next.push({
                event: "error",
                data: `Stream error: ${err.message}`,
                ts: ts(),
              });
            }
            return next;
          });
          setStreaming(false);
        },
      },
    );
  };

  // ── API key save + retry ───────────────────────────────────────────────────

  const handleSaveAndRetry = async () => {
    if (!activeSession || !pendingApiKeyProvider || !apiKeyInput.trim()) return;
    try {
      await callEndpoint(EP_SAVE_CONFIG, {
        provider: pendingApiKeyProvider,
        api_key: apiKeyInput.trim(),
      });
      setPendingApiKeyProvider(null);
      setApiKeyInput("");

      const message = pendingMessageRef.current;
      if (!message) return;

      setStreaming(true);
      streamBufferRef.current = "";
      const aiLineIndex = logLines.length;
      streamLineIndexRef.current = aiLineIndex;
      setLogLines((prev) => [
        ...prev,
        { event: "message", data: "AI: ", ts: ts() },
      ]);

      await callStreamingEndpoint(
        EP_SEND_MESSAGE,
        { session_id: activeSession.session_id, message },
        {
          onEvent(event, data) {
            if (event === "token") {
              streamBufferRef.current += data;
              const accumulated = streamBufferRef.current;
              setLogLines((prev) => {
                const next = [...prev];
                const idx = streamLineIndexRef.current;
                if (idx >= 0 && idx < next.length) {
                  next[idx] = { ...next[idx], data: `AI: ${accumulated}` };
                }
                return next;
              });
            } else if (event === "skill_saved") {
              const [skillName] = data.split(":");
              appendLog("status", `Skill updated: ${skillName}`);
            } else if (event === "api_key_required") {
              setPendingApiKeyProvider(data);
            } else if (event === "error") {
              appendLog("error", `Error: ${data}`);
            }
          },
          onDone() {
            setStreaming(false);
            streamBufferRef.current = "";
            streamLineIndexRef.current = -1;
          },
          onError(err) {
            appendLog("error", `Stream error: ${err.message}`);
            setStreaming(false);
          },
        },
      );
    } catch (e) {
      appendLog("error", `Failed to save API key: ${e}`);
    }
  };

  // ── Context upload ─────────────────────────────────────────────────────────

  const handleUploadContext = async () => {
    if (!activeSession || !contextText.trim()) return;
    try {
      await callEndpoint(EP_UPLOAD_CONTEXT, {
        session_id: activeSession.session_id,
        content_type: "text",
        content: contextText.trim(),
      });
      appendLog("status", "Training context injected.");
      setContextText("");
      setShowContextPanel(false);
    } catch (e) {
      appendLog("error", `Upload failed: ${e}`);
    }
  };

  // ── Key handler: Enter to send, Shift+Enter for newline ───────────────────

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const modeOptions = [
    { label: "Train", value: "train" },
    { label: "Ask", value: "ask" },
  ];

  const modelOptions = availableModels.map((m) => ({
    label: m.name,
    value: m.id,
  }));

  const sessionOptions = sessions.map((s) => ({
    label: `${s.title} [${s.mode}]`,
    value: s.session_id,
  }));

  return (
    <Container direction='column'>
      {/* ── Top bar ── */}
      <Navbar title='AI Chat' />

      {/* ── Session setup (shown when no active session) ── */}
      {!activeSession && (
        <Container direction='column'>
          <TextArea
            label='Session title'
            value={newTitle}
            onChange={setNewTitle}
            rows={1}
            placeholder='Give this session a name…'
          />
          {!forcedMode && (
            <Selector
              label='Mode'
              value={selectedMode}
              options={modeOptions}
              onChange={(v) => setSelectedMode(v as "train" | "ask")}
            />
          )}
          <Selector
            label='Model'
            value={selectedModel}
            options={
              modelOptions.length
                ? modelOptions
                : [{ label: selectedModel, value: selectedModel }]
            }
            onChange={setSelectedModel}
          />
          <ButtonGroup
            buttons={[
              {
                label: "New Chat",
                variant: "primary",
                action: { kind: "ui", handler: handleNewSession },
              },
            ]}
          />

          {sessions.length > 0 && (
            <>
              <Selector
                label='Resume existing session'
                value=''
                placeholder='Select a session…'
                options={sessionOptions}
                onChange={(id) => id && handleResumeSession(id)}
              />
              <ButtonGroup
                buttons={sessions.slice(0, 5).map((s) => ({
                  label: `Delete: ${s.title}`,
                  variant: "danger" as const,
                  action: {
                    kind: "ui" as const,
                    handler: () => handleDeleteSession(s.session_id),
                  },
                }))}
                orientation='vertical'
              />
            </>
          )}
        </Container>
      )}

      {/* ── Active chat ── */}
      {activeSession && (
        <Container direction='column'>
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <strong>{activeSession.title}</strong>
            <span style={{ opacity: 0.6, fontSize: 12 }}>
              [{activeSession.mode} / {activeSession.model_id}]
            </span>
            <ButtonGroup
              buttons={[
                {
                  label: "← Sessions",
                  variant: "ghost",
                  action: { kind: "ui", handler: () => setActiveSession(null) },
                },
                ...(activeSession.mode === "train"
                  ? [
                      {
                        label: showContextPanel
                          ? "Hide context"
                          : "+ Training context",
                        variant: "secondary" as const,
                        action: {
                          kind: "ui" as const,
                          handler: () => setShowContextPanel((v) => !v),
                        },
                      },
                    ]
                  : []),
              ]}
            />
          </div>

          {/* Mode + model switchers — key on session_id only so selector remounts
               when session changes but NOT on every model/mode change, preventing
               spurious onChange fires from uncontrolled remounting. */}
          {!forcedMode && (
            <Selector
              key={`mode-${activeSession.session_id}`}
              label='Mode'
              value={activeSession.mode}
              options={modeOptions}
              onChange={(v) => handleUpdateSession("mode", v)}
            />
          )}
          <Selector
            key={`model-${activeSession.session_id}`}
            label='Model'
            value={activeSession.model_id}
            options={
              modelOptions.length
                ? modelOptions
                : [
                    {
                      label: activeSession.model_id,
                      value: activeSession.model_id,
                    },
                  ]
            }
            onChange={(v) => handleUpdateSession("model_id", v)}
          />

          {/* Context upload panel (Train mode only) */}
          {showContextPanel && activeSession.mode === "train" && (
            <Container>
              <TextArea
                label='Paste training text or a URL to learn from'
                value={contextText}
                onChange={setContextText}
                rows={5}
                placeholder='Paste a pricing table, instructions, or a reference URL…'
              />
              <ButtonGroup
                buttons={[
                  {
                    label: "Inject into session",
                    variant: "primary",
                    action: { kind: "ui", handler: handleUploadContext },
                  },
                  {
                    label: "Cancel",
                    variant: "ghost",
                    action: {
                      kind: "ui",
                      handler: () => setShowContextPanel(false),
                    },
                  },
                ]}
              />
            </Container>
          )}

          {/* API key prompt */}
          {pendingApiKeyProvider && (
            <Container direction='column'>
              <div style={{ color: "#c0392b", fontWeight: 600 }}>
                {pendingApiKeyProvider.charAt(0).toUpperCase() +
                  pendingApiKeyProvider.slice(1)}{" "}
                API key required
              </div>
              <TextArea
                label={`${pendingApiKeyProvider.toUpperCase()}_API_KEY`}
                value={apiKeyInput}
                onChange={setApiKeyInput}
                rows={1}
                placeholder='Paste your API key here…'
              />
              <ButtonGroup
                buttons={[
                  {
                    label: "Save & Retry",
                    variant: "primary",
                    disabled: !apiKeyInput.trim(),
                    action: { kind: "ui", handler: handleSaveAndRetry },
                  },
                  {
                    label: "Cancel",
                    variant: "ghost",
                    action: {
                      kind: "ui",
                      handler: () => {
                        setPendingApiKeyProvider(null);
                        setApiKeyInput("");
                      },
                    },
                  },
                ]}
              />
            </Container>
          )}

          {/* Conversation log */}
          <LogPanel lines={logLines} running={streaming} />

          {/* Input area */}
          <div onKeyDown={handleKeyDown}>
            <TextArea
              value={inputText}
              onChange={setInputText}
              placeholder={
                activeSession.mode === "ask"
                  ? "Ask a question…"
                  : "Say something to train the AI… (Enter to send)"
              }
              rows={3}
              disabled={streaming}
            />
          </div>
          <ButtonGroup
            buttons={[
              {
                label: streaming ? "Thinking…" : "Send",
                variant: "primary",
                disabled: streaming || !inputText.trim(),
                action: { kind: "ui", handler: handleSend },
              },
            ]}
          />
        </Container>
      )}
    </Container>
  );
}
