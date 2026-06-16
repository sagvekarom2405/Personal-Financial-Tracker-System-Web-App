document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("loginForm");
  const message = document.getElementById("message");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value.trim();

    if (!email || !password) {
      message.innerText = "Please fill in all fields.";
      message.style.color = "red";
      return;
    }

    try {
      const res = await fetch("/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ email, password })
      });

      const data = await res.json();
      message.innerText = data.message;
      message.style.color = res.ok ? "green" : "red";

      if (res.ok) {
        setTimeout(() => {
          window.location.href = "/dashboard"; // Redirect after success
        }, 1000);
      }
    } catch (err) {
      message.innerText = "Error connecting to server.";
      message.style.color = "red";
    }
  });
});
