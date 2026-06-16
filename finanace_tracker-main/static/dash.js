function showSection(sectionId) {
  const sections = document.querySelectorAll(".section");
  const navItems = document.querySelectorAll(".nav li");

  sections.forEach((sec) => {
    sec.classList.remove("active-section");
  });
  document.getElementById(sectionId).classList.add("active-section");

  navItems.forEach((item) => item.classList.remove("active"));
  event.target.classList.add("active");
}
