import { renderMatchingPairs } from "./renderers/matching_pairs.js";
import { renderImageCards } from "./renderers/image_cards.js";
import { renderTouchListenCards } from "./renderers/touch_listen_cards.js";

async function main(){
  const res = await fetch("./game.json", { cache: "no-store" });
  const game = await res.json();

  const root = document.getElementById("app");
  const r = game?.meta?.renderer;

  if (r === "matching_pairs") return renderMatchingPairs(root, game);
  if (r === "image_cards") return renderImageCards(root, game);
  if (r === "touch_listen_cards") return renderTouchListenCards(root, game);

  root.innerHTML = `<div style="padding:16px">Unknown renderer: ${r}</div>`;
}
main();