import { renderMatchingPairs } from "./renderers/matching_pairs.js";
import { renderImageCards } from "./renderers/image_cards.js";
import { renderTouchListenCards } from "./renderers/touch_listen_cards.js";

function renderError(root, message){
  root.innerHTML = `<div style="padding:18px;color:#e5e7eb;font-family:system-ui,sans-serif;line-height:1.5">${message}</div>`;
}

async function loadGame(){
  const embedded = document.getElementById("game-data");
  if (embedded && embedded.textContent && embedded.textContent.trim()) {
    return JSON.parse(embedded.textContent);
  }
  const res = await fetch("./game.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load game.json (${res.status})`);
  return await res.json();
}

async function main(){
  const root = document.getElementById("app");
  try {
    const game = await loadGame();
    const r = game?.meta?.renderer;
    if (r === "matching_pairs") return renderMatchingPairs(root, game);
    if (r === "image_cards") return renderImageCards(root, game);
    if (r === "touch_listen_cards") return renderTouchListenCards(root, game);
    renderError(root, `Unknown renderer: ${String(r || "")}`);
  } catch (err) {
    renderError(root, `Could not load this tag_s pack. ${err instanceof Error ? err.message : String(err)}`);
  }
}
main();
