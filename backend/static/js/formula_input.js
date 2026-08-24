/**
 * formula_input.js — behaviour for the shared formula editor component
 * (templates/components/formula_editor.html).
 *
 * Every [data-formula-editor] on the page becomes an independent editor: chip
 * and operator buttons insert at the caret of *its own* input, and each keeps
 * its own undo history. Typing is coalesced into one undo step per burst.
 *
 * Auto-initialises on DOMContentLoaded; call window.initFormulaEditors() again
 * after inserting markup dynamically.
 */
(function () {
  "use strict";

  const TYPING_PAUSE_MS = 500;

  function initFormulaEditor(root) {
    if (root.dataset.formulaEditorReady) return;
    root.dataset.formulaEditorReady = "1";

    const input = root.querySelector("[data-formula-input]");
    if (!input) return;

    const undoBtn = root.querySelector("[data-formula-undo]");
    const clearBtn = root.querySelector("[data-formula-clear]");
    const undoStack = [];
    let typingBase = input.value;
    let typingTimer = null;

    function updateControls() {
      if (undoBtn) undoBtn.disabled = !undoStack.length;
      if (clearBtn) clearBtn.disabled = !input.value;
      [undoBtn, clearBtn].forEach((b) => {
        if (b) b.classList.toggle("opacity-40", b.disabled);
      });
    }

    function commitTyping() {
      if (typingTimer) {
        clearTimeout(typingTimer);
        typingTimer = null;
      }
      if (input.value !== typingBase) {
        undoStack.push(typingBase);
        typingBase = input.value;
      }
    }

    function snapshot() {
      commitTyping();
      undoStack.push(input.value);
    }

    function caretRange() {
      const len = input.value.length;
      return [
        input.selectionStart == null ? len : input.selectionStart,
        input.selectionEnd == null ? len : input.selectionEnd,
      ];
    }

    function replaceRange(start, end, text, caret) {
      input.value = input.value.slice(0, start) + text + input.value.slice(end);
      typingBase = input.value;
      input.focus();
      input.setSelectionRange(caret, caret);
      updateControls();
    }

    function insert(text) {
      snapshot();
      const [start, end] = caretRange();
      replaceRange(start, end, text, start + text.length);
    }

    function insertParens() {
      snapshot();
      const [start, end] = caretRange();
      const selected = input.value.slice(start, end);
      replaceRange(start, end, "(" + selected + ")", start + 1 + selected.length);
    }

    root.querySelectorAll("[data-insert]").forEach((btn) => {
      btn.addEventListener("click", () => insert(btn.getAttribute("data-insert")));
    });
    root.querySelectorAll("[data-insert-parens]").forEach((btn) => {
      btn.addEventListener("click", insertParens);
    });

    if (undoBtn) {
      undoBtn.addEventListener("click", () => {
        commitTyping();
        if (!undoStack.length) return;
        input.value = undoStack.pop();
        typingBase = input.value;
        input.focus();
        updateControls();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        if (!input.value) return;
        snapshot();
        input.value = "";
        typingBase = "";
        input.focus();
        updateControls();
      });
    }

    input.addEventListener("input", () => {
      if (typingTimer) clearTimeout(typingTimer);
      typingTimer = setTimeout(() => {
        typingTimer = null;
        if (input.value !== typingBase) {
          undoStack.push(typingBase);
          typingBase = input.value;
          updateControls();
        }
      }, TYPING_PAUSE_MS);
      updateControls();
    });

    updateControls();
  }

  function initFormulaEditors() {
    document.querySelectorAll("[data-formula-editor]").forEach(initFormulaEditor);
  }

  window.initFormulaEditors = initFormulaEditors;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFormulaEditors);
  } else {
    initFormulaEditors();
  }
})();
