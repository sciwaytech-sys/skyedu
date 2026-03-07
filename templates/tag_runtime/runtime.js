import { renderMatchingPairs } from "./renderers/matching_pairs.js";

async function main(){
  const res = await fetch("./game.json", { cache: "no-store" });
  const game = await res.json();

  const root = document.getElementById("app");
  const r = game?.meta?.renderer;

  if (r === "matching_pairs") return renderMatchingPairs(root, game);

  root.innerHTML = `<div style="padding:16px">Unknown renderer: ${r}</div>`;
}
main();