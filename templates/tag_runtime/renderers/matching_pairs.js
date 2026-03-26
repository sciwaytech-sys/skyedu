export function renderMatchingPairs(root, game){
  const meta = game.meta || {};
  const items = game.items || [];

  const title = meta.title || "tag_s";
  const tag = meta.tag || "";
  const gameId = meta.game_id || "";
  const players = new Map();

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

  const tiles = [];
  for (const it of items){
    tiles.push({ key: it.id, side: "a", text: it.a, audio: it.audio_a || "" });
    tiles.push({ key: it.id, side: "b", text: it.b, audio: it.audio_b || "" });
  }
  shuffle(tiles);

  let first = null;
  let lock = false;
  let moves = 0;
  let hits = 0;

  function playAudio(src){
    if(!src) return;
    let audio = players.get(src);
    if(!audio){ audio = new Audio(src); players.set(src, audio); }
    try{ audio.currentTime = 0; audio.play(); }catch(e){}
  }

  const nodes = tiles.map((t, idx) => {
    const div = document.createElement("div");
    div.className = "tile";
    div.innerHTML = `<div>${escapeHtml(t.text)}</div>${t.audio ? '<button class="mini-audio">▶</button>' : ''}`;
    div.dataset.key = t.key;
    div.dataset.idx = String(idx);
    div.addEventListener("click", (ev) => onPick(div, t.audio, ev));
    grid.appendChild(div);
    return div;
  });

  function onPick(node, audioSrc, ev){
    if (ev.target.closest('.mini-audio')){
      playAudio(audioSrc);
      return;
    }
    if(audioSrc) playAudio(audioSrc);
    if (lock) return;
    if (node.classList.contains("ok")) return;

    if (!first){
      first = node;
      node.classList.add("ok");
      return;
    }

    if (node === first) return;

    moves += 1;
    movesEl.textContent = `Moves: ${moves}`;

    const k1 = first.dataset.key;
    const k2 = node.dataset.key;

    if (k1 && k2 && k1 === k2){
      hits += 1;
      hitsEl.textContent = `Hits: ${hits}/${items.length}`;
      node.classList.add("ok");
      first = null;
      return;
    }

    lock = true;
    node.classList.add("bad");
    first.classList.add("bad");
    setTimeout(() => {
      node.classList.remove("bad");
      first.classList.remove("bad");
      first.classList.remove("ok");
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
