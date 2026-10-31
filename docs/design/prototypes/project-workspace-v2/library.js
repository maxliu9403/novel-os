const toast = document.querySelector("#demo-toast");
let toastTimer;
document.querySelectorAll("[data-demo]").forEach((control) => {
  control.addEventListener("click", (event) => {
    event.preventDefault();
    clearTimeout(toastTimer);
    toast.textContent = control.dataset.demo;
    toast.classList.add("is-visible");
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3200);
  });
});
document.querySelector("#library-search").addEventListener("input", (event) => {
  const query = event.target.value.trim().toLowerCase();
  const match = "boots by the door audrey ellingford".includes(query);
  document.querySelector("#sample-book").hidden = !match;
  document.querySelector("#library-empty").hidden = match;
});
