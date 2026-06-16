document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("goalForm");

  form?.addEventListener("submit", function (e) {
    e.preventDefault();

    const data = {
      title: document.getElementById("title").value,
      category: document.getElementById("category").value,
      target_amount: parseFloat(document.getElementById("target_amount").value),
      current_amount: parseFloat(document.getElementById("current_amount").value),
      target_date: document.getElementById("target_date").value
    };

    fetch("/add-goal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data)
    })
      .then(res => res.json())
      .then(res => {
        if (res.status === "success") {
          window.location.href = "/goals";  // Go back to main goals page
        }
      });
  });
});
