(() => {
  const maxItems = 5;
  const maxImageBytes = 10 * 1024 * 1024;
  const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
  const container = document.querySelector("#items-container");
  const addButton = document.querySelector("#add-item");
  const countInput = document.querySelector("#item-count");

  const setPreview = (input) => {
    const uploadBox = input.closest(".field").querySelector(".upload-box");
    const name = uploadBox.querySelector("[data-upload-name]");
    const hint = uploadBox.querySelector("[data-upload-hint]");
    const preview = uploadBox.querySelector("[data-upload-preview]");
    const error = input.closest(".field").querySelector("[data-upload-error]");
    const previousUrl = input.dataset.previewUrl;
    if (previousUrl) {
      URL.revokeObjectURL(previousUrl);
      delete input.dataset.previewUrl;
    }

    input.setCustomValidity("");
    error.hidden = true;
    error.textContent = "";
    preview.hidden = true;
    preview.removeAttribute("src");

    const file = input.files[0];
    if (!file) {
      name.textContent = "Escolha uma imagem";
      hint.textContent = "Obrigatória · JPG, PNG ou WEBP · até 10 MB";
      return;
    }
    if (!allowedTypes.has(file.type)) {
      input.setCustomValidity("Escolha uma imagem JPG, PNG ou WEBP.");
      error.textContent = "Formato não aceito. Escolha uma imagem JPG, PNG ou WEBP.";
    } else if (file.size > maxImageBytes) {
      input.setCustomValidity("A imagem deve ter no máximo 10 MB.");
      error.textContent = "Esta imagem excede o limite de 10 MB.";
    } else {
      const previewUrl = URL.createObjectURL(file);
      input.dataset.previewUrl = previewUrl;
      preview.src = previewUrl;
      preview.hidden = false;
      hint.textContent = `${(file.size / 1024 / 1024).toFixed(2)} MB · pronta para envio`;
    }

    if (input.validationMessage) {
      error.hidden = false;
      input.value = "";
      name.textContent = "Escolha outra imagem";
      hint.textContent = "Obrigatória · JPG, PNG ou WEBP · até 10 MB";
    } else {
      name.textContent = file.name;
    }
  };

  container.addEventListener("change", (event) => {
    if (event.target.matches('input[type="file"]')) setPreview(event.target);
  });

  const requestForm = document.querySelector(".request-form");
  requestForm.addEventListener("submit", (event) => {
    if (!requestForm.reportValidity()) event.preventDefault();
  });

  addButton.addEventListener("click", () => {
    const index = container.children.length;
    if (index >= maxItems) return;

    const section = document.createElement("section");
    section.className = "item-card";
    section.dataset.itemIndex = String(index);
    section.innerHTML = `
      <div class="item-top"><h3><span class="item-number">${index + 1}</span> Item ${index + 1}</h3>
        <button class="remove-item" type="button" aria-label="Remover item ${index + 1}">Remover ×</button></div>
      <label class="field">Resumo do item<input type="text" name="item_summary_${index}" maxlength="200" placeholder="Ex.: Cadeira de escritório ergonômica" required></label>
      <label class="field">Detalhes do item<textarea name="item_details_${index}" rows="4" maxlength="5000" placeholder="Descreva características, medidas, materiais ou outras informações importantes." required></textarea><span class="hint">Até 5.000 caracteres</span></label>
      <label class="field">Imagem do item (obrigatória)
        <span class="upload-box">
          <span class="upload-icon">↑</span>
          <span class="upload-copy"><strong data-upload-name>Escolha uma imagem</strong><small data-upload-hint>Obrigatória · JPG, PNG ou WEBP · até 10 MB</small></span>
          <input type="file" name="item_image_${index}" accept="image/jpeg,image/png,image/webp" aria-label="Imagem do item ${index + 1}" required>
          <img class="upload-preview" data-upload-preview alt="Prévia da imagem selecionada" hidden>
        </span>
        <span class="upload-error" data-upload-error role="status" hidden></span>
      </label>`;
    section.querySelector(".remove-item").addEventListener("click", () => {
      section.remove();
      [...container.children].forEach((item, itemIndex) => {
        item.dataset.itemIndex = String(itemIndex);
        item.querySelector(".item-number").textContent = String(itemIndex + 1);
        item.querySelector("h3").lastChild.textContent = ` Item ${itemIndex + 1}`;
        item.querySelector('input[type="text"]').name = `item_summary_${itemIndex}`;
        item.querySelector("textarea").name = `item_details_${itemIndex}`;
        item.querySelector('input[type="file"]').name = `item_image_${itemIndex}`;
        item.querySelector('input[type="file"]').setAttribute("aria-label", `Imagem do item ${itemIndex + 1}`);
        const remove = item.querySelector(".remove-item");
        if (remove) remove.setAttribute("aria-label", `Remover item ${itemIndex + 1}`);
      });
      countInput.value = String(container.children.length);
      addButton.hidden = container.children.length >= maxItems;
    });
    container.append(section);
    countInput.value = String(container.children.length);
    addButton.hidden = container.children.length >= maxItems;
  });
})();
