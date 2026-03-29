(() => {
  if (typeof window === "undefined" || window.bootstrap) {
    return;
  }

  const DROPDOWN_SELECTOR = '[data-bs-toggle="dropdown"]';
  const COLLAPSE_SELECTOR = '[data-bs-toggle="collapse"]';

  const closeDropdown = (toggle) => {
    const container = toggle.closest(".dropdown, .btn-group");
    const menu = container?.querySelector(".dropdown-menu");
    if (!container || !menu) {
      return;
    }
    container.classList.remove("show");
    menu.classList.remove("show");
    toggle.setAttribute("aria-expanded", "false");
  };

  const openDropdown = (toggle) => {
    const container = toggle.closest(".dropdown, .btn-group");
    const menu = container?.querySelector(".dropdown-menu");
    if (!container || !menu) {
      return;
    }
    container.classList.add("show");
    menu.classList.add("show");
    toggle.setAttribute("aria-expanded", "true");
  };

  const closeAllDropdowns = (exceptToggle = null) => {
    document.querySelectorAll(DROPDOWN_SELECTOR).forEach((toggle) => {
      if (exceptToggle && toggle === exceptToggle) {
        return;
      }
      closeDropdown(toggle);
    });
  };

  document.addEventListener("click", (event) => {
    const dropdownToggle = event.target.closest(DROPDOWN_SELECTOR);
    if (dropdownToggle) {
      event.preventDefault();
      const expanded = dropdownToggle.getAttribute("aria-expanded") === "true";
      closeAllDropdowns(dropdownToggle);
      if (!expanded) {
        openDropdown(dropdownToggle);
      } else {
        closeDropdown(dropdownToggle);
      }
      return;
    }

    if (!event.target.closest(".dropdown-menu")) {
      closeAllDropdowns();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllDropdowns();
    }
  });

  document.addEventListener("click", (event) => {
    const collapseToggle = event.target.closest(COLLAPSE_SELECTOR);
    if (!collapseToggle) {
      return;
    }

    const targetSelector =
      collapseToggle.getAttribute("data-bs-target") ||
      collapseToggle.getAttribute("href");
    if (!targetSelector || !targetSelector.startsWith("#")) {
      return;
    }

    const target = document.querySelector(targetSelector);
    if (!target) {
      return;
    }

    event.preventDefault();
    const shouldShow = !target.classList.contains("show");
    target.classList.toggle("show", shouldShow);
    collapseToggle.setAttribute("aria-expanded", shouldShow ? "true" : "false");
  });
})();
