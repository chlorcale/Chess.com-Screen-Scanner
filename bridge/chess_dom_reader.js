(() => {
  const CONFIG = {
    endpoint: "http://127.0.0.1:8765/moves",
    debounceMs: 120,
    maxRows: 5000,
  };

  function allElements(root = document) {
    const out = [];
    const walk = (node) => {
      if (!node || !node.querySelectorAll) return;
      for (const el of node.querySelectorAll("*")) {
        out.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
      if (node.shadowRoot) walk(node.shadowRoot);
    };
    walk(root);
    return out;
  }

  function findMoveLists() {
    const direct = [...document.querySelectorAll("wc-simple-move-list")];
    const deep = allElements(document).filter(
      el => el.tagName && el.tagName.toLowerCase() === "wc-simple-move-list"
    );
    const merged = [...direct, ...deep];
    return [...new Set(merged)];
  }

  function clean(text) {
    return String(text ?? "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function rowMoves(row) {
    const number = Number(row.dataset?.wholeMoveNumber);
    if (!Number.isInteger(number) || number <= 0) return null;

    const white = row.querySelector(
      ".white-move .node-highlight-content"
    )?.textContent;
    const black = row.querySelector(
      ".black-move .node-highlight-content"
    )?.textContent;

    const result = {
      number,
      white: clean(white),
      black: clean(black),
    };

    if (!result.white && !result.black) return null;
    return result;
  }

  function chooseMoveList() {
    const lists = findMoveLists();
    let best = null;
    let bestCount = -1;

    for (const list of lists) {
      const count = list.querySelectorAll?.(
        ".main-line-row[data-whole-move-number]"
      )?.length ?? 0;
      if (count > bestCount) {
        best = list;
        bestCount = count;
      }
    }

    return best;
  }

  function readMoves() {
    const list = chooseMoveList();
    if (!list) {
      return {
        rows: [],
        text: "",
        boardId: null,
        error: "Không tìm thấy wc-simple-move-list",
      };
    }

    const rows = [...list.querySelectorAll(
      ".main-line-row[data-whole-move-number]"
    )]
      .slice(0, CONFIG.maxRows)
      .map(rowMoves)
      .filter(Boolean)
      .sort((a, b) => a.number - b.number);

    const text = rows.map(row => {
      const parts = [];
      if (row.white) parts.push(`${row.number}. ${row.white}`);
      if (row.black) parts.push(row.black);
      return parts.join(" ");
    }).join(" ");

    return {
      rows,
      text,
      boardId: list.getAttribute("board-id"),
      error: null,
    };
  }

  let lastPayload = "";
  let timer = null;
  let observer = null;
  let historyKey = "";
  const historyRows = new Map();

  function mergeHistory(data) {
    const nextKey = `${location.href}|${data.boardId || ""}`;
    const first = data.rows.find(r => r.number === 1);
    const oldFirst = historyRows.get(1);

    // Same page/board but move 1 changed: treat it as a new game.
    const firstChanged = first && oldFirst && (
      first.white !== oldFirst.white || first.black !== oldFirst.black
    );

    // URL or board-id changed: new game/component.
    if (historyKey && nextKey !== historyKey) {
      historyRows.clear();
    }

    if (firstChanged) {
      historyRows.clear();
    }

    historyKey = nextKey;

    for (const row of data.rows) {
      historyRows.set(row.number, row);
    }

    const rows = [...historyRows.values()].sort((a, b) => a.number - b.number);
    const text = rows.map(row => {
      const parts = [];
      if (row.white) parts.push(`${row.number}. ${row.white}`);
      if (row.black) parts.push(row.black);
      return parts.join(" ");
    }).join(" ");

    return { ...data, rows, text };
  }

  async function emit(force = false) {
    const data = mergeHistory(readMoves());
    const payload = JSON.stringify({
      source: "chess.com-dom",
      timestamp: Date.now(),
      url: location.href,
      boardId: data.boardId,
      rows: data.rows,
      text: data.text,
      error: data.error,
    });

    if (!force && payload === lastPayload) return;
    lastPayload = payload;

    try {
      const response = await fetch(CONFIG.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: payload,
      });
      if (!response.ok) {
        console.warn("[Chess DOM Reader] HTTP", response.status);
      }
    } catch (err) {
      console.warn(
        "[Chess DOM Reader] Không kết nối được local bridge.",
        err
      );
    }

    window.__CHESS_DOM_MOVES__ = data;
    console.log("%c[CHESS DOM]", "color:#0b5fa5;font-weight:bold", data.text);
  }

  function scheduleEmit() {
    clearTimeout(timer);
    timer = setTimeout(() => emit(false), CONFIG.debounceMs);
  }

  function stop() {
    clearTimeout(timer);
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    console.log("[Chess DOM Reader] stopped");
  }

  function start() {
    stop();
    const list = chooseMoveList();
    if (!list) {
      console.warn(
        "[Chess DOM Reader] Chưa tìm thấy wc-simple-move-list."
      );
      return;
    }

    observer = new MutationObserver(scheduleEmit);
    observer.observe(list, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["class", "data-whole-move-number"],
    });

    emit(true);

    console.log(
      "[Chess DOM Reader] started on",
      list,
      "board-id=",
      list.getAttribute("board-id")
    );
  }

  window.__ChessDOMReader = {
    start,
    stop,
    readMoves,
    emit: () => emit(true),
    get text() {
      return readMoves().text;
    },
  };

  start();
})();
