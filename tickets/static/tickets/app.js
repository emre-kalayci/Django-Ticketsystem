(() => {
  const german = document.documentElement.lang.startsWith("de");
  const text = (english, translated) => (german ? translated : english);
  const csrfToken = () => {
    const token = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="));
    return token ? decodeURIComponent(token.split("=")[1]) : "";
  };

  document.querySelectorAll("[data-menu]").forEach((menu) => {
    const trigger = menu.querySelector("[data-menu-trigger]");
    const content = menu.querySelector("[data-menu-content]");
    trigger?.addEventListener("click", () => {
      const willOpen = content.hidden;
      content.hidden = !willOpen;
      trigger.setAttribute("aria-expanded", String(willOpen));
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) {
        content.hidden = true;
        trigger?.setAttribute("aria-expanded", "false");
      }
    });
  });

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-auto-submit]").forEach((form) => {
    form.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => {
        form.setAttribute("aria-busy", "true");
        form.requestSubmit();
      });
    });
  });

  const initializeEditor = (root) => {
    const source = document.getElementById(root.dataset.inputId);
    const uploadInput = document.getElementById(root.dataset.uploadInputId);
    const editor = root.querySelector(".rich-editor-content");
    const uploadStatus = root.querySelector("[data-upload-status]");
    if (!source || !editor) return;

    const sync = () => {
      source.value = editor.innerHTML;
    };
    const insertHtml = (html) => {
      editor.focus();
      document.execCommand("insertHTML", false, html);
      sync();
    };
    root.querySelectorAll("[data-command]").forEach((button) => {
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => {
        const command = button.dataset.command;
        editor.focus();
        if (command === "createLink") {
          const href = window.prompt(
            text(
              "Link URL (https:// or mailto:)",
              "Link-URL (https:// oder mailto:)",
            ),
          );
          if (href) document.execCommand("createLink", false, href);
        } else if (command === "taskList") {
          insertHtml(
            '<ul data-type="taskList"><li data-checked="false">Task</li></ul><p><br></p>',
          );
        } else {
          document.execCommand(command, false, button.dataset.value || null);
        }
        sync();
      });
    });
    editor.addEventListener("input", sync);
    editor.addEventListener("blur", sync);

    const uploadImages = async (files) => {
      const images = [...files].filter((file) =>
        ["image/png", "image/jpeg"].includes(file.type),
      );
      if (!images.length) {
        uploadStatus.textContent = text(
          "Only PNG and JPEG images can be embedded.",
          "Nur PNG- und JPEG-Bilder können eingebettet werden.",
        );
        return;
      }
      for (const image of images) {
        uploadStatus.textContent = german
          ? `${image.name} wird hochgeladen…`
          : `Uploading ${image.name}…`;
        const body = new FormData();
        body.append("file", image);
        try {
          const response = await fetch(root.dataset.uploadUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken() },
            body,
            credentials: "same-origin",
          });
          const payload = await response.json();
          if (!response.ok) {
            throw new Error(
              payload.error ||
                text("Image upload failed.", "Bild-Upload fehlgeschlagen."),
            );
          }
          insertHtml(
            `<img src="${payload.url}" alt="${image.name.replace(/[<>\"]/g, "")}"><p><br></p>`,
          );
          if (uploadInput) {
            const current = JSON.parse(uploadInput.value || "[]");
            current.push(payload.id);
            uploadInput.value = JSON.stringify(current);
          }
          uploadStatus.textContent = german
            ? `${image.name} wurde hochgeladen.`
            : `${image.name} uploaded.`;
        } catch (error) {
          uploadStatus.textContent = error.message;
        }
      }
    };
    editor.addEventListener("drop", (event) => {
      if (event.dataTransfer?.files?.length) {
        event.preventDefault();
        uploadImages(event.dataTransfer.files);
      }
    });
    editor.addEventListener("paste", (event) => {
      const files = event.clipboardData?.files;
      if (files?.length) {
        event.preventDefault();
        uploadImages(files);
      }
    });
    root.closest("form")?.addEventListener("submit", sync);
    sync();
  };
  document.querySelectorAll("[data-rich-editor]").forEach(initializeEditor);

  const toggle = document.querySelector("[data-description-toggle]");
  const view = document.querySelector("[data-description-view]");
  const edit = document.querySelector("[data-description-edit]");
  const viewSidebar = document.querySelector("[data-description-view-sidebar]");
  const editSidebar = document.querySelector("[data-description-edit-sidebar]");
  const editIcon = document.querySelector("[data-edit-icon]");
  const viewIcon = document.querySelector("[data-view-icon]");
  const setEditing = (editing) => {
    if (!view || !edit || !toggle) return;
    view.hidden = editing;
    edit.hidden = !editing;
    if (viewSidebar) viewSidebar.hidden = editing;
    if (editSidebar) editSidebar.hidden = !editing;
    editIcon.hidden = editing;
    viewIcon.hidden = !editing;
    const label = editing
      ? text("Preview description", "Beschreibung anzeigen")
      : text("Edit description", "Beschreibung bearbeiten");
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
  };
  toggle?.addEventListener("click", () => setEditing(edit.hidden));
  document
    .querySelector("[data-description-cancel]")
    ?.addEventListener("click", () => setEditing(false));
  if (
    edit?.querySelector(".errorlist, .field-error, .form-error") ||
    editSidebar?.querySelector(".errorlist, .field-error, .form-error")
  ) {
    setEditing(true);
  }
})();
