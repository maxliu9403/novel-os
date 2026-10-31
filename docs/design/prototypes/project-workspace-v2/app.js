const tabs = [...document.querySelectorAll("[data-tab]")];
const panels = [...document.querySelectorAll("[data-panel]")];

function activateTab(id, focus = false) {
  const tab = tabs.find((item) => item.dataset.tab === id) || tabs.find((item) => item.dataset.tab === "chapters");
  const selected = tab.dataset.tab;
  tabs.forEach((item) => {
    const active = item === tab;
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
  });
  panels.forEach((panel) => {
    const active = panel.dataset.panel === selected;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  const url = new URL(window.location.href);
  url.searchParams.set("tab", selected);
  window.history.replaceState(null, "", url);
  if (focus) tab.focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    activateTab(tabs[next].dataset.tab, true);
  });
});

document.querySelectorAll("[data-go]").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.go, true));
});

const initialTab = new URL(window.location.href).searchParams.get("tab");
activateTab(tabs.some((tab) => tab.dataset.tab === initialTab) ? initialTab : "chapters");

const filterButtons = [...document.querySelectorAll("[data-filter]")];
const chapterRows = [...document.querySelectorAll(".chapter-row")];
const chapterSearch = document.querySelector("#chapter-search");
const emptyFilter = document.querySelector("#empty-filter");
let chapterFilter = "all";

function filterChapters() {
  const query = chapterSearch.value.trim().toLocaleLowerCase("zh-CN");
  let visible = 0;
  chapterRows.forEach((row) => {
    const statusMatch = chapterFilter === "all" || row.dataset.status === chapterFilter;
    const searchMatch = !query || row.dataset.search.toLocaleLowerCase("zh-CN").includes(query);
    row.hidden = !(statusMatch && searchMatch);
    if (!row.hidden) visible += 1;
  });
  emptyFilter.hidden = visible !== 0;
}

filterButtons.forEach((button) => {
  button.addEventListener("click", () => {
    chapterFilter = button.dataset.filter;
    filterButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    filterChapters();
  });
});
chapterSearch.addEventListener("input", filterChapters);

const characters = {
  riley: {
    index: "01",
    name: "Riley",
    role: "主角 · 选择与边界",
    description: "她在寒假前发现 Dane 背叛，面对父母要求她为金钱继续忍耐的压力，选择保存截图并离开。雪崩封路后，她必须在失联、情绪失控和临时同住中继续保护自己的判断与边界。",
    facts: [["当前目标", "保留证据，重新取得选择权"], ["核心压力", "家庭经济要求、背叛与封路"], ["与 Lily", "真实挚友关系，道德问题未决"], ["与 Kaleb", "临时共同生存者；不是爱情线"]],
    source: "采用骨架 · Chapters 1–3 与 Relationships",
  },
  kaleb: {
    index: "02",
    name: "Kaleb",
    role: "木屋主人 · 临时共同生存者",
    description: "Lily 的哥哥。Riley 抵达木屋时，他已经提前为 Nora 的圣诞监护安排做准备。道路封闭后，他与 Riley 从主人/客人的关系转为临时共同生存者。骨架明确要求不要把这段关系呈现成爱情线。",
    facts: [["木屋位置", "主人"], ["家庭责任", "为 Nora 的圣诞监护做准备"], ["与 Riley", "主人/客人、临时共同生存者"], ["与 Marisol", "前伴侣与共同家长"]],
    source: "采用骨架 · Chapter 2 与 Relationships",
  },
  lily: {
    index: "03",
    name: "Lily",
    role: "Riley 的挚友",
    description: "她邀请 Riley 去山间木屋。两人从小学一年级起就是挚友，后来成为大学室友；Riley 把她们视为 chosen sisters。骨架同时保留这段友谊的道德悬念，不提前给出和解结论。",
    facts: [["关系起点", "小学一年级"], ["共同经历", "大学室友"], ["Riley 的理解", "chosen sisters"], ["关系状态", "真实，但道德问题未决"]],
    source: "采用骨架 · Riley ↔ Lily",
  },
  marisol: {
    index: "04",
    name: "Marisol",
    role: "Kaleb 的前伴侣与共同家长",
    description: "她与 Kaleb 有共同养育关系。读者看到的过往仅来自不完整叙述，因此页面只展示骨架明确写出的关系，不补充立场、责任或情感判断。",
    facts: [["与 Kaleb", "前伴侣"], ["家庭关系", "共同家长"], ["叙事限制", "只通过不完整叙述出现"], ["过往信息", "仅有部分叙述"]],
    source: "采用骨架 · Kaleb ↔ Marisol",
  },
};

const characterDetail = document.querySelector("#character-detail");
const characterButtons = [...document.querySelectorAll("[data-character]")];

function renderCharacter(id) {
  const character = characters[id];
  characterDetail.dataset.index = character.index;
  characterDetail.innerHTML = `
    <span class="eyebrow">骨架人物</span>
    <h3>${character.name}</h3>
    <span class="character-role">${character.role}</span>
    <p>${character.description}</p>
    <div class="character-facts">
      ${character.facts.map(([label, value]) => `<div><span>${label}</span><b>${value}</b></div>`).join("")}
    </div>
    <span class="source-tag">${character.source}</span>
  `;
  characterButtons.forEach((button) => {
    const active = button.dataset.character === id;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

characterButtons.forEach((button) => button.addEventListener("click", () => renderCharacter(button.dataset.character)));
renderCharacter("riley");

const quotes = {
  "riley-kaleb": "best friend’s older brother, host/guest, temporary co-survivors, one consensual intimacy, then estrangement. This is not a romance arc…",
  "riley-lily": "best friends since first grade, college roommates, chosen sisters in Riley’s mind. The friendship is real and remains morally unresolved.",
  "kaleb-marisol": "former partners and co-parents, with a history the reader sees only through partial accounts.",
};
const quoteDialog = document.querySelector("#quote-dialog");
const quoteText = document.querySelector("#quote-text");
document.querySelectorAll("[data-quote]").forEach((button) => {
  button.addEventListener("click", () => {
    quoteText.textContent = quotes[button.dataset.quote];
    if (typeof quoteDialog.showModal === "function") quoteDialog.showModal();
    else quoteDialog.setAttribute("open", "");
  });
});
document.querySelector(".dialog-close").addEventListener("click", () => quoteDialog.close());
quoteDialog.addEventListener("click", (event) => {
  if (event.target === quoteDialog) quoteDialog.close();
});

const demoToast = document.querySelector("#demo-toast");
let toastTimer;
document.querySelectorAll("[data-demo]").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    window.clearTimeout(toastTimer);
    demoToast.textContent = `${button.dataset.demo}。`;
    demoToast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => demoToast.classList.remove("is-visible"), 2400);
  });
});
