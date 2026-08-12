(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  let channels = [];
  let activeChannelId = null;
  let eventSource = null;
  let isWaitingForReply = false;

  const renderedEventIds = new Set();
  const activeStreams = new Map();
  const closedCallIds = new Set();

  let thinkingCallId = null;
  let thinkingElement = null;
  let thinkingBuffer = "";
  let thinkingWordCount = 0;

  // ---------------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------------

  const $channelList      = document.getElementById("channel-list");
  const $emptyState       = document.getElementById("empty-state");
  const $chatView         = document.getElementById("chat-view");
  const $chatChannelName  = document.getElementById("chat-channel-name");
  const $messages         = document.getElementById("messages");
  const $typingIndicator  = document.getElementById("typing-indicator");
  const $messageForm      = document.getElementById("message-form");
  const $messageInput     = document.getElementById("message-input");
  const $btnNewChannel    = document.getElementById("btn-new-channel");
  const $modalOverlay     = document.getElementById("modal-overlay");
  const $newChannelForm   = document.getElementById("new-channel-form");
  const $newChannelName   = document.getElementById("new-channel-name");
  const $btnCancelModal   = document.getElementById("btn-cancel-modal");
  const $wakeupOverlay    = document.getElementById("wakeup-overlay");
  const $executionBanner  = document.getElementById("execution-banner");
  const $toggleThinking   = document.getElementById("toggle-thinking");

  let showThinking = true;

  // ---------------------------------------------------------------------------
  // API helpers
  // ---------------------------------------------------------------------------

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (opts.method === "DELETE" && res.status === 204) return null;
    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Channels
  // ---------------------------------------------------------------------------

  async function loadChannels() {
    channels = await api("/api/channels");
    renderChannelList();
  }


  // ---------------------------------------------------------------------------
  // Account panel: who you are, and what agents may act as on your behalf
  // ---------------------------------------------------------------------------

  async function loadAccount() {
    const $panel = document.getElementById("account-panel");
    const $email = document.getElementById("account-email");
    const $connections = document.getElementById("account-connections");
    if (!$panel) return;

    let me;
    try {
      me = await api("/api/me");
    } catch (err) {
      return;
    }
    if (!me || !me.email) return;   // password mode or signed out: nothing to show

    $panel.classList.remove("hidden");
    $email.textContent = me.email;

    const connected = (me.connected || []).includes("google");
    $connections.innerHTML = connected
      ? `<span class="account-connected">Google connected</span>
         <button id="btn-disconnect-google" class="account-link">Disconnect</button>`
      : `<a class="account-connect" href="/auth/google/connect">Connect Google</a>
         <span class="account-hint">for mail &amp; calendar</span>`;

    const $disconnect = document.getElementById("btn-disconnect-google");
    if ($disconnect) {
      $disconnect.addEventListener("click", async () => {
        await api("/auth/google/disconnect", { method: "POST" });
        loadAccount();
      });
    }
  }

  function renderChannelList() {
    $channelList.innerHTML = "";
    channels.forEach((ch) => {
      const li = document.createElement("li");
      li.className = "channel-item" + (ch.id === activeChannelId ? " active" : "");
      li.dataset.id = ch.id;
      li.innerHTML =
        '<span class="hash">#</span>' +
        '<span class="channel-name">' + escapeHtml(ch.name) + "</span>" +
        '<button class="channel-delete" title="Delete conversation" aria-label="Delete conversation">' +
        '<span class="trash-icon" aria-hidden="true"></span></button>';
      li.addEventListener("click", () => selectChannel(ch.id));
      li.querySelector(".channel-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteChannel(ch.id);
      });
      $channelList.appendChild(li);
    });
  }

  function showMessageSkeleton() {
    $messages.querySelectorAll(".message, .skeleton-group, .tool-activity").forEach((el) => el.remove());
    const skeleton = document.createElement("div");
    skeleton.className = "skeleton-group";
    for (let i = 0; i < 4; i++) {
      skeleton.innerHTML += `
        <div class="skeleton-msg">
          <div class="skeleton-avatar skeleton-pulse"></div>
          <div class="skeleton-body">
            <div class="skeleton-line skeleton-line-name skeleton-pulse"></div>
            <div class="skeleton-line skeleton-pulse" style="width:${65 + Math.random() * 30}%"></div>
            <div class="skeleton-line skeleton-pulse" style="width:${40 + Math.random() * 35}%"></div>
          </div>
        </div>`;
    }
    $messages.insertBefore(skeleton, $typingIndicator);
  }

  function removeMessageSkeleton() {
    $messages.querySelectorAll(".skeleton-group").forEach((el) => el.remove());
  }

  async function selectChannel(channelId) {
    if (activeChannelId === channelId) return;
    activeChannelId = channelId;
    renderedEventIds.clear();
    activeStreams.forEach((s) => s.element?.remove());
    activeStreams.clear();
    closedCallIds.clear();
    if (thinkingElement) thinkingElement.remove();
    thinkingCallId = null;
    thinkingElement = null;
    thinkingBuffer = "";
    thinkingWordCount = 0;
    renderChannelList();
    triggerWakeup();

    const known = channels.find((c) => c.id === channelId);
    $emptyState.classList.add("hidden");
    $chatView.classList.remove("hidden");
    $chatChannelName.textContent = known ? known.name : "";
    $messageInput.placeholder = known ? `Message #${known.name}` : "Message #channel";
    $messageInput.disabled = true;
    showMessageSkeleton();

    const ch = await api(`/api/channels/${channelId}`);
    removeMessageSkeleton();
    $messageInput.disabled = false;

    if (!ch || ch.error) return;

    $chatChannelName.textContent = ch.name;
    $messageInput.placeholder = `Message #${ch.name}`;

    (ch.messages || []).forEach((msg) => renderMessage(msg));
    scrollToBottom();
    subscribeSSE(channelId);
    setTyping(false);
    setExecuting(false);
    $messageInput.focus();
  }

  // ---------------------------------------------------------------------------
  // SSE
  // ---------------------------------------------------------------------------

  function subscribeSSE(channelId) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    eventSource = new EventSource(`/api/channels/${channelId}/events`);
    eventSource.onmessage = (e) => {
      if (channelId !== activeChannelId) return;
      try {
        const data = JSON.parse(e.data);
        handleSSEEvent(data);
      } catch (_) { /* ignore parse errors */ }
    };
    eventSource.onerror = () => {
      // Browser will auto-reconnect for us
    };
  }

  function handleSSEEvent(data) {
    if (data.type === "image_generated") {
      const msg = data.message;
      if (!msg) return;
      if (msg.event_id && renderedEventIds.has(msg.event_id)) return;
      renderMessage(msg);
      scrollToBottom();
    } else if (data.type === "llm_stream_chunk") {
      handleStreamChunk(data);
    } else if (data.type === "llm_thinking_chunk") {
      handleThinkingChunk(data);
    } else if (data.type === "tool_usage_started") {
      setTyping(false);
      createToolActivity(data.agent_role, data.tool_name);
      scrollToBottom();
    } else if (data.type === "tool_usage_finished") {
      handleToolUsageFinished(data);
    } else if (data.type === "tool_usage_error") {
      handleToolUsageError(data);
    } else if (data.type === "kickoff_started" || data.type === "flow_started") {
      setTyping(true);
      setExecuting(true);
    } else if (data.type === "conversation_route_selected") {
      createRouteActivity(data.route);
      scrollToBottom();
    } else if (data.type === "conversation_turn_completed") {
      handleFlowFinished(data);
    } else if (data.type === "flow_finished") {
      handleFlowFinished(data);
    } else if (data.type === "kickoff_error") {
      finalizeAllStreams();
      setTyping(false);
      setExecuting(false);
      showError(data.error || "Kickoff failed");
    }
  }

  // ---------------------------------------------------------------------------
  // Messages
  // ---------------------------------------------------------------------------

  function renderMessage(msg) {
    if (msg.role === "tool") return;
    if (msg.event_id) renderedEventIds.add(msg.event_id);

    if (msg.event_type === "tool_usage") {
      return renderToolUsageFromDB(msg);
    }

    const div = document.createElement("div");
    div.className = "message";
    if (msg.event_type === "thinking") {
      div.classList.add("thinking");
      if (!showThinking) div.classList.add("hidden");
    }

    const roleLabel = msg.role === "user" ? "You" : msg.role === "assistant" ? "CrewAI" : "Tool";
    const isCrewAI = msg.role === "assistant" || msg.role === "tool";
    const avatarHtml = isCrewAI
      ? '<img src="/static/img/crewai-logo.svg" alt="CrewAI" class="avatar-logo">'
      : "U";
    const ts = formatTimestamp(msg.timestamp);

    let contentHtml = "";
    if (msg.event_type === "image_generated" && msg.image_base64) {
      contentHtml = `<img class="message-image" src="data:image/png;base64,${msg.image_base64}" alt="Generated image" loading="lazy">`;
      if (msg.content) {
        contentHtml = `<div class="message-content">${renderContent(msg)}</div>` + contentHtml;
      }
    } else {
      contentHtml = `<div class="message-content">${renderContent(msg)}</div>`;
    }

    div.innerHTML = `
      <div class="message-avatar ${msg.role}">${avatarHtml}</div>
      <div class="message-body">
        <div class="message-header">
          <span class="message-author ${msg.role}">${roleLabel}</span>
          <span class="message-timestamp">${ts}</span>
        </div>
        ${contentHtml}
      </div>
    `;

    $messages.insertBefore(div, $typingIndicator);

    const img = div.querySelector(".message-image");
    if (img) img.addEventListener("load", scrollToBottom);
    return div;
  }

  function renderToolUsageFromDB(msg) {
    let duration_s = null;
    if (msg.timeline) {
      try {
        const t = JSON.parse(msg.timeline);
        duration_s = t.duration_s;
      } catch (_) {}
    }
    const displayName = humanizeToolName(msg.content);
    const durationText = duration_s != null ? ` for ${Number(duration_s).toFixed(1)}s` : "";
    const div = document.createElement("div");
    div.className = "tool-activity done";
    div.dataset.tool = msg.content;
    div.innerHTML = `
      <img src="/static/img/tool.svg" class="tool-activity-icon" />
      <span class="tool-activity-text">Used <strong>${escapeHtml(displayName)}</strong>${durationText}</span>
    `;
    $messages.insertBefore(div, $typingIndicator);
    return div;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      $messages.scrollTop = $messages.scrollHeight;
    });
  }

  function setTyping(show) {
    isWaitingForReply = show;
    $typingIndicator.classList.toggle("hidden", !show);
    if (show) scrollToBottom();
  }

  function setExecuting(show) {
    $executionBanner.classList.toggle("hidden", !show);
  }

  // ---------------------------------------------------------------------------
  // Streaming
  // ---------------------------------------------------------------------------

  function setCursorOn(el) {
    if (!el) return;
    $messages.querySelectorAll(".cursor-active").forEach((e) => {
      if (e !== el) e.classList.remove("cursor-active");
    });
    el.classList.add("cursor-active");
  }

  function clearCursors() {
    $messages.querySelectorAll(".cursor-active").forEach((e) => e.classList.remove("cursor-active"));
  }

  function handleStreamChunk(data) {
    const { call_id, chunk, tool_call, agent_role, seq } = data;
    if (!call_id || !chunk) return;

    if (tool_call) return;

    if (closedCallIds.has(call_id)) return;

    const key = call_id;
    let stream = activeStreams.get(key);

    if (!stream) {
      const isStructuredOutput = chunk.trimStart().startsWith("{") || chunk.trimStart().startsWith("[");
      if (isStructuredOutput) {
        activeStreams.set(key, { structured: true });
        return;
      }

      setTyping(false);
      const el = createStreamingBubble(agent_role);
      const contentEl = el.querySelector(".message-content");
      stream = {
        element: el, contentEl,
        chunks: new Map(),
        fallbackSeq: 0,
        contentBuffer: "",
        wordCount: 0,
        agentRole: agent_role, callId: call_id,
        startTime: Date.now(),
      };
      activeStreams.set(key, stream);
    }

    if (stream.structured) return;

    setTyping(false);

    const seqKey = (typeof seq === "number") ? seq : `f${stream.fallbackSeq++}`;
    if (stream.chunks.has(seqKey)) return;
    stream.chunks.set(seqKey, chunk);
    stream.contentBuffer = [...stream.chunks.entries()]
      .sort((a, b) => {
        const na = typeof a[0] === "number", nb = typeof b[0] === "number";
        if (na && nb) return a[0] - b[0];
        if (na) return -1;
        if (nb) return 1;
        return 0;
      })
      .map((e) => e[1])
      .join("");

    if (stream.contentEl && stream.contentBuffer) {
      stream.contentEl.innerHTML = marked.parse(stream.contentBuffer, { breaks: true });

      const walker = document.createTreeWalker(stream.contentEl, NodeFilter.SHOW_TEXT);
      const textNodes = [];
      while (walker.nextNode()) textNodes.push(walker.currentNode);

      let wordIdx = 0;
      let newWordIdx = 0;

      for (const node of textNodes) {
        const text = node.textContent;
        if (!text) continue;

        const tokens = text.split(/(\s+)/);
        const fragment = document.createDocumentFragment();

        for (const token of tokens) {
          if (!token) continue;
          if (/^\s+$/.test(token)) {
            fragment.appendChild(document.createTextNode(token));
          } else {
            const span = document.createElement("span");
            if (wordIdx < stream.wordCount) {
              span.className = "stream-word-done";
            } else {
              span.className = "stream-word";
              span.style.animationDelay = `${newWordIdx * 25}ms`;
              newWordIdx++;
            }
            span.textContent = token;
            fragment.appendChild(span);
            wordIdx++;
          }
        }

        node.parentNode.replaceChild(fragment, node);
      }

      stream.wordCount = wordIdx;
      setCursorOn(stream.contentEl);
    }
    scrollToBottom();
  }

  function handleThinkingChunk(data) {
    const { chunk, call_id, agent_role } = data;
    if (!chunk) return;

    setTyping(false);
    clearCursors();

    if (call_id !== thinkingCallId) {
      thinkingCallId = call_id;
      thinkingBuffer = "";
      thinkingWordCount = 0;
      thinkingElement = createStreamingBubble(agent_role || "CrewAI");
      thinkingElement.classList.add("thinking");
      if (!showThinking) thinkingElement.classList.add("hidden");
    }
    thinkingBuffer += chunk;

    const contentEl = thinkingElement.querySelector(".message-content");
    contentEl.innerHTML = marked.parse(thinkingBuffer, { breaks: true });

    const walker = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);

    let wordIdx = 0;
    let newWordIdx = 0;

    for (const node of textNodes) {
      const text = node.textContent;
      if (!text) continue;

      const tokens = text.split(/(\s+)/);
      const fragment = document.createDocumentFragment();

      for (const token of tokens) {
        if (!token) continue;
        if (/^\s+$/.test(token)) {
          fragment.appendChild(document.createTextNode(token));
        } else {
          const span = document.createElement("span");
          if (wordIdx < thinkingWordCount) {
            span.className = "think-word-done";
          } else {
            span.className = "think-word";
            span.style.animationDelay = `${newWordIdx * 30}ms`;
            newWordIdx++;
          }
          span.textContent = token;
          fragment.appendChild(span);
          wordIdx++;
        }
      }

      node.parentNode.replaceChild(fragment, node);
    }

    thinkingWordCount = wordIdx;
    contentEl.scrollTop = contentEl.scrollHeight;
    scrollToBottom();
  }

  function handleToolUsageFinished(data) {
    const el = document.querySelector(
      `.tool-activity[data-tool="${data.tool_name}"]:not(.done)`
    );
    if (el) {
      el.classList.add("done");
      const textEl = el.querySelector(".tool-activity-text");
      if (textEl) {
        textEl.innerHTML = `Used <strong>${escapeHtml(humanizeToolName(data.tool_name))}</strong> for ${Number(data.duration_s).toFixed(1)}s`;
      }
      const spinner = el.querySelector(".tool-activity-spinner");
      if (spinner) spinner.remove();
    }
    scrollToBottom();
  }

  function handleToolUsageError(data) {
    console.error(`[ToolUsageError] ${data.tool_name}: ${data.error}`);
    const el = document.querySelector(
      `.tool-activity[data-tool="${data.tool_name}"]:not(.done)`
    );
    if (el) {
      el.classList.add("done", "error");
      const textEl = el.querySelector(".tool-activity-text");
      if (textEl) {
        textEl.innerHTML = `<strong>${escapeHtml(humanizeToolName(data.tool_name))}</strong> failed`;
      }
      const spinner = el.querySelector(".tool-activity-spinner");
      if (spinner) spinner.remove();
    }
    scrollToBottom();
  }

  function createStreamingBubble(agentRole) {
    const div = document.createElement("div");
    div.className = "message";

    const avatarHtml = '<img src="/static/img/crewai-logo.svg" alt="CrewAI" class="avatar-logo">';
    const ts = formatTimestamp(new Date().toISOString());

    div.innerHTML = `
      <div class="message-avatar assistant">${avatarHtml}</div>
      <div class="message-body">
        <div class="message-header">
          <span class="message-author assistant">CrewAI</span>
          <span class="message-timestamp">${ts}</span>
        </div>
        <div class="message-content streaming"></div>
      </div>
    `;

    $messages.insertBefore(div, $typingIndicator);
    return div;
  }

  function createToolActivity(agentRole, toolName) {
    const div = document.createElement("div");
    div.className = "tool-activity";
    div.dataset.tool = toolName;

    const displayName = humanizeToolName(toolName);
    div.innerHTML = `
      <img src="/static/img/tool.svg" class="tool-activity-icon" />
      <span class="tool-activity-text">Using <strong>${escapeHtml(displayName)}</strong>...</span>
      <div class="tool-activity-spinner"><span></span><span></span><span></span></div>
    `;

    $messages.insertBefore(div, $typingIndicator);
    return div;
  }

  // Shows which handler the router picked. `converse` is the built-in chat
  // route, so label it as such rather than leaking the internal name.
  function createRouteActivity(route) {
    if (!route) return null;

    const div = document.createElement("div");
    div.className = "tool-activity route-activity";
    div.dataset.route = route;

    const label = route === "converse" ? "Conversation" : humanizeToolName(route);
    div.innerHTML = `
      <img src="/static/img/tool.svg" class="tool-activity-icon" />
      <span class="tool-activity-text">Routed to <strong>${escapeHtml(label)}</strong></span>
    `;

    $messages.insertBefore(div, $typingIndicator);
    return div;
  }

  function handleFlowFinished(data) {
    if (data.call_id) closedCallIds.add(data.call_id);
    if (data.text) applyFinalText(data.call_id, data.text);
    finalizeAllStreams();
    setTyping(false);
    setExecuting(false);
  }

  function applyFinalText(callId, text) {
    let stream = (callId && activeStreams.get(callId)) || null;
    if (!stream) stream = activeStreams.values().next().value || null;

    let contentEl;
    if (stream && stream.contentEl) {
      contentEl = stream.contentEl;
    } else {
      const el = createStreamingBubble("");
      contentEl = el.querySelector(".message-content");
    }

    contentEl.innerHTML = marked.parse(text, { breaks: true });
    contentEl.style.display = "";
    contentEl.classList.remove("streaming", "cursor-active");
    scrollToBottom();
  }

  function finalizeAllStreams() {
    for (const [, stream] of activeStreams) {
      finalizeStream(stream);
    }
    activeStreams.clear();
    thinkingCallId = null;
    thinkingElement = null;
    thinkingBuffer = "";
    thinkingWordCount = 0;
    scrollToBottom();
  }

  function finalizeStream(stream) {
    if (stream.contentEl) {
      stream.contentEl.classList.remove("streaming", "cursor-active");
      if (!stream.contentBuffer) stream.contentEl.style.display = "none";
    }
  }

  function humanizeToolName(name) {
    if (!name) return "a tool";
    return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  $messageForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = $messageInput.value.trim();
    if (!content || !activeChannelId) return;

    $messageInput.value = "";

    try {
      const result = await api(`/api/channels/${activeChannelId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content }),
      });
      // api() resolves on non-2xx too, so an error body has to be checked
      // explicitly — otherwise the typing indicator spins forever.
      if (result && result.error) {
        showError(result.error);
        return;
      }
      if (result && result.message) {
        renderMessage(result.message);
        scrollToBottom();
      }
      setTyping(true);
    } catch (err) {
      showError("Failed to send message");
    }
  });

  // ---------------------------------------------------------------------------
  // New channel modal
  // ---------------------------------------------------------------------------

  $btnNewChannel.addEventListener("click", () => {
    $modalOverlay.classList.remove("hidden");
    $newChannelName.value = "";
    $newChannelName.focus();
  });

  $btnCancelModal.addEventListener("click", closeModal);
  $modalOverlay.addEventListener("click", (e) => {
    if (e.target === $modalOverlay) closeModal();
  });

  function closeModal() {
    $modalOverlay.classList.add("hidden");
  }

  $newChannelForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $newChannelName.value.trim();
    if (!name) return;

    closeModal();
    const ch = await api("/api/channels", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    if (ch && ch.id) {
      await loadChannels();
      selectChannel(ch.id);
    }
  });

  // ---------------------------------------------------------------------------
  // Delete channel
  // ---------------------------------------------------------------------------

  async function deleteChannel(channelId) {
    if (!confirm("Delete this channel and all its messages?")) return;

    await api(`/api/channels/${channelId}`, { method: "DELETE" });

    if (channelId === activeChannelId) {
      activeChannelId = null;
      if (eventSource) { eventSource.close(); eventSource = null; }
      $chatView.classList.add("hidden");
      $emptyState.classList.remove("hidden");
    }
    await loadChannels();
  }

  // ---------------------------------------------------------------------------
  // AMP wakeup
  // ---------------------------------------------------------------------------

  function triggerWakeup() {
    fetch("/api/wakeup", { method: "POST" })
      .then((res) => res.json())
      .then((data) => {
        if (data.status === "waking") {
          $wakeupOverlay.classList.remove("hidden", "fade-out");
        }
      })
      .catch(() => {})
      .finally(() => {
        $wakeupOverlay.classList.add("fade-out");
        setTimeout(() => $wakeupOverlay.classList.add("hidden"), 400);
      });
  }

  // ---------------------------------------------------------------------------
  // Utilities
  // ---------------------------------------------------------------------------

  function renderContent(msg) {
    if (!msg.content) return "";
    if (msg.role === "user") return escapeHtml(msg.content);
    return marked.parse(msg.content, { breaks: true });
  }

  function escapeHtml(str) {
    if (!str) return "";
    const el = document.createElement("span");
    el.textContent = str;
    return el.innerHTML;
  }

  function formatTimestamp(ts) {
    if (!ts) return "";
    try {
      const d = new Date(ts.includes("T") ? ts : ts + "Z");
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
    } catch (_) {
      return ts;
    }
  }

  function showError(msg) {
    const el = document.createElement("div");
    el.className = "error-toast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  // ---------------------------------------------------------------------------
  // Thinking toggle
  // ---------------------------------------------------------------------------

  $toggleThinking.addEventListener("change", () => {
    showThinking = $toggleThinking.checked;
    $messages.querySelectorAll(".message.thinking").forEach((el) => {
      el.classList.toggle("hidden", !showThinking);
    });
  });

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------

  triggerWakeup();
  loadChannels();
  loadAccount();
})();
