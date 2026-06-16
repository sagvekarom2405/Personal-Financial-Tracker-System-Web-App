document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("signupForm");
  const message = document.getElementById("message");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fullName = document.getElementById("full_name").value.trim();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value.trim();

    const res = await fetch("/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, full_name: fullName })
    });

    const data = await res.json();
    message.innerText = data.message;
    message.style.color = res.ok ? "green" : "red";

    if (res.ok) {
      setTimeout(() => {
        window.location.href = "/";
      }, 1000);
    }
  });
});
