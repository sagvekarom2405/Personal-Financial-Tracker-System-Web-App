document.getElementById("openFormBtn").addEventListener("click", () => {
  document.getElementById("goalFormContainer").classList.remove("hidden");
});

document.getElementById("cancelBtn").addEventListener("click", () => {
  document.getElementById("goalFormContainer").classList.add("hidden");
});

document.getElementById("goalForm").addEventListener("submit", async function (e) {
  e.preventDefault();

  const formData = new FormData(this);
  const goalData = Object.fromEntries(formData.entries());

  const response = await fetch("/add-goal", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(goalData),
  });

  const result = await response.json();
  if (result.success) {
    location.reload(); // Reload to see new goal
  } else {
    alert("Error adding goal.");
  }
});
