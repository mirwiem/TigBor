const savedTheme = localStorage.getItem("tigbor-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
document.getElementById("themeToggle")?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("tigbor-theme", next);
});
