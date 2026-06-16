document.addEventListener("DOMContentLoaded", () => {
  const transactionForm = document.getElementById("transactionForm");
  const suggestCategoryBtn = document.getElementById("suggestCategoryBtn");
  const parseTransactionBtn = document.getElementById("parseTransactionBtn");
  const suggestionMessageEl = document.getElementById("ai-suggestion-message");
  const parseMessageEl = document.getElementById("ai-parse-message");
  const quickEntryInput = document.getElementById("ai-transaction-input");
  const amountInput = document.getElementById("amount");
  const descriptionInput = document.getElementById("description");
  const categorySelect = document.getElementById("category");
  const dateInput = document.getElementById("date");
  if (!transactionForm) {
    console.error("The element #transactionForm was not found in your HTML.");
    return;
  }

  // Set today's date as default
  if (dateInput) {
    const today = new Date().toISOString().split('T')[0];
    dateInput.value = today;
  }

  const selectedTypeValue = () => {
    const selectedType = document.querySelector('input[name="type"]:checked');
    return selectedType ? selectedType.value : "";
  };

  const showSuggestion = (text, isError = false) => {
    if (!suggestionMessageEl) return;
    suggestionMessageEl.textContent = text;
    suggestionMessageEl.style.color = isError ? "#b91c1c" : "#334155";
  };

  const showParseMessage = (text, isError = false) => {
    if (!parseMessageEl) return;
    parseMessageEl.textContent = text;
    parseMessageEl.style.color = isError ? "#b91c1c" : "#334155";
  };

  let suggestDebounceTimer = null;
  let suggestAbortController = null;
  let lastSuggestionKey = "";

  const buildSuggestionKey = (type, amount, description) => {
    return `${type}|${String(amount || "").trim()}|${String(description || "").trim().toLowerCase()}`;
  };

  const requestSuggestion = async () => {
    const type = selectedTypeValue();
    const amount = amountInput ? amountInput.value : "";
    const description = descriptionInput ? descriptionInput.value : "";

    if (!type) {
      showSuggestion("Select income or expense first.", true);
      return;
    }

    if (!description.trim() && !amount) {
      showSuggestion("Add a short description or amount so I can make a useful suggestion.", true);
      return;
    }

    showSuggestion("Thinking about the best category...");
    const currentKey = buildSuggestionKey(type, amount, description);

    if (suggestAbortController) {
      suggestAbortController.abort();
    }
    suggestAbortController = new AbortController();

    try {
      const response = await fetch("/ai/suggest-transaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, amount, description }),
        signal: suggestAbortController.signal
      });

      const result = await response.json();
      if (!response.ok || !result.success) {
        showSuggestion(result.message || "Could not generate a suggestion right now.", true);
        return;
      }

      if (categorySelect) {
        categorySelect.value = result.category;
      }

      lastSuggestionKey = currentKey;
      showSuggestion(`Suggested "${result.category.replace("_", " ")}" with ${result.confidence} confidence. ${result.reason}`);
    } catch (error) {
      if (error.name === "AbortError") {
        return;
      }
      console.error(error);
      showSuggestion("Suggestion failed. Please try again.", true);
    }
  };

  const applyParsedTransaction = (result) => {
    const targetTypeInput = document.querySelector(`input[name="type"][value="${result.type}"]`);
    if (targetTypeInput) {
      targetTypeInput.checked = true;
    }

    if (amountInput && Number.isFinite(Number(result.amount))) {
      amountInput.value = result.amount;
    }

    if (dateInput && result.date) {
      dateInput.value = result.date;
    }

    if (descriptionInput && result.description) {
      descriptionInput.value = result.description;
    }

    if (categorySelect && result.category) {
      categorySelect.value = result.category;
    }

    lastSuggestionKey = buildSuggestionKey(result.type, result.amount, result.description);
  };

  const requestParse = async () => {
    const text = quickEntryInput ? quickEntryInput.value.trim() : "";

    if (!text) {
      showParseMessage("Write one sentence first so I can parse it.", true);
      return;
    }

    showParseMessage("Reading your transaction...");

    try {
      const response = await fetch("/ai/parse-transaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });

      const result = await response.json();
      if (!response.ok || !result.success) {
        showParseMessage(result.message || "Could not parse that transaction.", true);
        return;
      }

      applyParsedTransaction(result);
      showParseMessage(`Filled the form. ${result.reason}`);
      showSuggestion(`Suggested "${result.category.replace("_", " ")}" with ${result.confidence} confidence. ${result.reason}`);
    } catch (error) {
      console.error(error);
      showParseMessage("Parsing failed. Please try again.", true);
    }
  };

  const requestSuggestionDebounced = () => {
    const type = selectedTypeValue();
    const amount = amountInput ? amountInput.value : "";
    const description = descriptionInput ? descriptionInput.value : "";

    if (!type || (!String(description).trim() && !String(amount).trim())) {
      return;
    }

    const currentKey = buildSuggestionKey(type, amount, description);
    if (currentKey === lastSuggestionKey) {
      return;
    }

    if (suggestDebounceTimer) {
      clearTimeout(suggestDebounceTimer);
    }
    suggestDebounceTimer = setTimeout(() => {
      requestSuggestion();
    }, 550);
  };

  if (suggestCategoryBtn) {
    suggestCategoryBtn.addEventListener("click", requestSuggestion);
  }

  if (parseTransactionBtn) {
    parseTransactionBtn.addEventListener("click", requestParse);
  }

  const typeInputs = document.querySelectorAll('input[name="type"]');
  typeInputs.forEach((input) => {
    input.addEventListener("change", requestSuggestionDebounced);
  });

  if (amountInput) {
    amountInput.addEventListener("input", requestSuggestionDebounced);
  }

  if (descriptionInput) {
    descriptionInput.addEventListener("input", requestSuggestionDebounced);
  }

  transactionForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    // A dedicated element for showing messages is better than alert()
    const messageEl = document.getElementById("transaction-message");
    if (!messageEl) {
      console.error("The element #transaction-message was not found in your HTML.");
      return;
    }
    
    // Function to show message
    const showMessage = (text, isError = false) => {
      messageEl.textContent = text;
      messageEl.style.display = "block";
      messageEl.style.color = isError ? "red" : "green";
      messageEl.style.backgroundColor = isError ? "#ffebee" : "#e8f5e8";
      messageEl.style.border = isError ? "1px solid #f44336" : "1px solid #4caf50";
    };

    // Clear previous messages
    messageEl.style.display = "none";
    messageEl.textContent = "";

    // --- Form Data & Validation ---
    const selectedType = document.querySelector('input[name="type"]:checked');
    const amount = document.getElementById("amount").value;
    const category = document.getElementById("category").value;
    const date = document.getElementById("date").value;

    if (!selectedType) {
      showMessage("Please select a transaction type (Income or Expense).", true);
      return;
    }

    if (!amount || parseFloat(amount) <= 0) {
      showMessage("Please enter a valid positive amount.", true);
      return;
    }

    if (!category.trim()) {
      showMessage("Please select a category.", true);
      return;
    }

    if (!date) {
      showMessage("Please select a date.", true);
      return;
    }

    const data = {
      type: selectedType.value,
      amount: parseFloat(amount),
      category: category,
      date: date,
      description: document.getElementById("description").value,
    };

    try {
      // Show loading message
      showMessage("Processing transaction...", false);
      
      const response = await fetch("/transaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });

      // --- Enhanced Debugging ---
      console.log("--- Transaction Submission Debug Info ---");
      console.log("Request data:", data);
      console.log("Status:", response.status, response.statusText);
      console.log("OK:", response.ok);
      console.log("Redirected:", response.redirected);
      console.log("URL:", response.url);
      const responseText = await response.clone().text();
      console.log("Body (raw text):", responseText);
      console.log("----------------------------------------");

      // If the server redirected (e.g., to a login page due to an expired session),
      // this indicates an authentication issue. Let's navigate to the new page.
      if (response.redirected) {
        window.location.href = response.url;
        return;
      }

      // Try to parse the response as JSON. It might fail if the server
      // sent an HTML error page instead of a JSON error object.
      const result = await response.json().catch(() => null);

      if (!response.ok) {
        const errorMessage = result?.message || `Error: ${response.statusText}`;
        showMessage(errorMessage, true);
        return;
      }

      showMessage(result.message || "Transaction added successfully!");
      transactionForm.reset();
      lastSuggestionKey = "";
      showSuggestion("No suggestion yet.");
      
      // Redirect to dashboard after a short delay
      setTimeout(() => {
        window.location.href = "/dashboard";
      }, 1500);
      
    } catch (err) {
      showMessage("An error occurred. Please check the console for details.", true);
      console.error(err);
    }
  });
});
