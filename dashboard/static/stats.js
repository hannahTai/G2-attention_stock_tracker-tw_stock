// 統計頁的切換（歷史統計的期間、新增處置的每日／每週）不整頁重新載入：在背景抓新的頁面，只換掉那一塊，網址照樣跟著變。
// 連結上寫 data-swap="要換掉的那幾塊的 id"（空白隔開）。沒有 JavaScript、或抓失敗時，照樣是一般連結（整頁換）。
// 網站其他地方沒有 JavaScript，只有這裡（切換時畫面跳回頁首、閃一下，很干擾）。
let swapped = false;   // 這一頁換過內容沒（換過才需要在上一頁時重新載入）
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-swap]");
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;   // 另開分頁照常
  const ids = link.dataset.swap.split(/\s+/).filter(Boolean);
  const targets = ids.map((id) => document.getElementById(id));
  if (!targets.length || targets.some((t) => !t)) return;
  event.preventDefault();
  targets[0].setAttribute("aria-busy", "true");
  try {
    const response = await fetch(link.href, { headers: { Accept: "text/html" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = ids.map((id) => page.getElementById(id));
    if (fresh.some((f) => !f)) throw new Error("新的頁面少了某一塊");
    targets.forEach((t, i) => t.replaceWith(fresh[i]));
    history.pushState({ swapped: true }, "", link.href);   // 上一頁回到前一個樣子
    swapped = true;
  } catch {
    window.location.href = link.href;   // 抓不到就照一般連結整頁換
  }
});

// 按上一頁、下一頁：網址變了但畫面還是換過的那塊，整頁重新載入這個網址（簡單、不會對不上）
// 只看「跳到主要內容」這類 #錨點 的上一頁不用重新載入
window.addEventListener("popstate", (event) => {
  if (swapped || event.state?.swapped) window.location.reload();
});
