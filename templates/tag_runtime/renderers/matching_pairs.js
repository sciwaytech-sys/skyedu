export function renderMatchingPairs(root, game){
  const meta = game.meta || {};
  const items = game.items || []; // [{a:"apple", b:"苹果"}...]

  const title = meta.title || "tag_s";
  const tag = meta.tag || "";
  const gameId = meta.game_id || "";

  root.innerHTML = `
    <div class="wrap">
      <div class="h1">${escapeHtml(title)}</div>
      <div class="sub">${escapeHtml(tag)} · ${escapeHtml(gameId)} · Matching Pairs</div>
      <div class="bar">
        <div class="pill" id="moves">Moves: 0</div>
        <div class="pill" id="hits">Hits: 0/${items.length}</div>
      </div>
      <div class="grid" id="grid"></div>
    </div>
  `;

  const grid = root.querySelector("#grid");
  const movesEl = root.querySelector("#moves");
  const hitsEl = root.querySelector("#hits");

  // Build tiles: one for each side
  const tiles = [];
  for (const it of items){
    tiles.push({ key: it.id, side: "a", text: it.a });
    tiles.push({ key: it.id, side: "b", text: it.b });
  }
  shuffle(tiles);

  let first = null;
  let lock = false;
  let moves = 0;
  let hits = 0;

  const nodes = tiles.map((t, idx) => {
    const div = document.createElement("div");
    div.className = "tile";
    div.textContent = t.text;
    div.dataset.key = t.key;
    div.dataset.idx = String(idx);
    div.addEventListener("click", () => onPick(div));
    grid.appendChild(div);
    return div;
  });

  function onPick(node){
    if (lock) return;
    if (node.classList.contains("ok")) return;

    if (!first){
      first = node;
      node.classList.add("ok"); // highlight as selected
      return;
    }

    if (node === first) return;

    moves += 1;
    movesEl.textContent = `Moves: ${moves}`;

    const k1 = first.dataset.key;
    const k2 = node.dataset.key;

    if (k1 && k2 && k1 === k2){
      // matched
      hits += 1;
      hitsEl.textContent = `Hits: ${hits}/${items.length}`;
      node.classList.add("ok");
      // keep both selected as ok
      first = null;
      return;
    }

    // mismatch
    lock = true;
    node.classList.add("bad");
    first.classList.add("bad");
    setTimeout(() => {
      node.classList.remove("bad");
      first.classList.remove("bad");
      first.classList.remove("ok"); // remove selection highlight
      first = null;
      lock = false;
    }, 500);
  }
}

function shuffle(a){
  for (let i = a.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
}

function escapeHtml(s){
  return String(s || "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}