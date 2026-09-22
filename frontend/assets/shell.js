(function (global) {
  "use strict";

  var officialPages = {
    dashboard: { label: "Dashboard", href: "official-dashboard.html", icon: "dashboard" },
    operations: { label: "Operations", href: "new-compliance-scan.html", icon: "document_scanner" },
    history: { label: "History", href: "scan-history.html", icon: "history" },
    products: { label: "Products", href: "products.html", icon: "inventory_2" },
    settings: { label: "Settings", href: "settings.html", icon: "settings" }
  };
  var aliases = {
    "official-dashboard.html": "dashboard",
    "new-compliance-scan.html": "operations",
    "scan-history.html": "history",
    "review-queue.html": "history",
    "case-details.html": "history",
    "log-case-action.html": "history",
    "products.html": "products",
    "settings.html": "settings",
    "profile.html": "settings",
    "support.html": "support",
    "compliance-report.html": "history",
    "compliance-result.html": "operations"
  };

  function escape(value) {
    return global.LL && global.LL.escapeHtml ? global.LL.escapeHtml(value) : String(value || "");
  }

  function pageName() {
    return (global.location.pathname.split("/").pop() || "official-dashboard.html").toLowerCase();
  }

  function roleFor(session) {
    return "official";
  }

  function removeLegacyShell() {
    document.querySelectorAll("body > aside, body > nav.fixed, header, .ll-official-header, .ll-manufacturer-header").forEach(function (element) {
      element.remove();
    });
  }

  function render(user, role) {
    removeLegacyShell();
    var pages = officialPages;
    var active = aliases[pageName()] || "dashboard";
    var roleLabel = "OFFICIAL";
    var pageTitle = active === "support" ? "Support" : (pages[active] ? pages[active].label : "LegalLens");
    var dashboard = pages.dashboard.href;
    var operations = pages.operations.href;
    var nav = Object.keys(pages).map(function (key) {
      var item = pages[key];
      return '<a class="ll-sidebar-link' + (key === active ? ' is-active' : '') + '" href="' + item.href + '" data-nav="' + key + '"' + (key === active ? ' aria-current="page"' : '') + '><span class="material-symbols-outlined">' + item.icon + '</span><span>' + item.label + '</span></a>';
    }).join("");
    var sidebar = document.createElement("aside");
    sidebar.className = "ll-sidebar";
    sidebar.setAttribute("aria-label", roleLabel + " navigation");
    sidebar.innerHTML = '<div class="ll-sidebar-brand"><a href="' + dashboard + '"><img src="assets/page-previews/Logo.png" alt="LegalLens"><span><strong>LegalLens</strong><small>Government portal</small></span></a></div>' +
      '<a class="ll-new-scan" href="' + operations + '"><span class="material-symbols-outlined">add</span><span>New Compliance Scan</span></a>' +
      '<nav class="ll-sidebar-main" aria-label="Primary navigation">' + nav + '</nav>' +
      '<div class="ll-sidebar-bottom"><a class="ll-sidebar-link ll-support' + (active === "support" ? ' is-active' : '') + '" href="support.html"' + (active === "support" ? ' aria-current="page"' : '') + '><span class="material-symbols-outlined">help</span><span>Support</span></a><button class="ll-sidebar-link ll-logout" type="button"><span class="material-symbols-outlined">logout</span><span>Logout</span></button></div>';
    document.body.appendChild(sidebar);

    var header = document.createElement("header");
    header.className = "ll-header";
    header.innerHTML = '<button class="ll-menu-toggle" type="button" aria-label="Open navigation" aria-expanded="false"><span class="material-symbols-outlined">menu</span></button>' +
      '<div class="ll-header-title"><img class="ll-header-logo" src="assets/page-previews/Logo.png" alt="LegalLens"><span class="ll-demo-pill"' + (user.demoMode ? '' : ' hidden') + '>DEMO MODE</span><span>' + pageTitle + '</span></div>' +
      '<nav class="ll-header-nav" aria-label="Quick navigation"><a href="' + dashboard + '">Dashboard</a><a href="' + operations + '">New Scan</a><a href="scan-history.html">History</a><a href="review-queue.html">Review Queue</a></nav>' +
      '<div class="ll-header-account"><span>' + roleLabel + '</span><button class="ll-profile-button" type="button" aria-label="Open profile" aria-expanded="false"><span class="material-symbols-outlined">person</span></button><div class="ll-profile-menu" hidden><div class="ll-profile-heading"><span class="material-symbols-outlined">account_circle</span><span><strong>' + escape(user.fullName) + '</strong><small>' + escape(user.email) + '</small></span></div><a href="profile.html">Profile</a><a href="settings.html">Settings</a><button class="ll-menu-logout" type="button"><span class="material-symbols-outlined">logout</span>Logout</button></div></div>';
    document.body.appendChild(header);

    var overlay = document.createElement("div");
    overlay.className = "ll-sidebar-overlay";
    document.body.appendChild(overlay);
    var toggle = header.querySelector(".ll-menu-toggle");
    function closeSidebar() { document.body.classList.remove("ll-sidebar-open"); toggle.setAttribute("aria-expanded", "false"); }
    toggle.addEventListener("click", function () { document.body.classList.toggle("ll-sidebar-open"); toggle.setAttribute("aria-expanded", String(document.body.classList.contains("ll-sidebar-open"))); });
    overlay.addEventListener("click", closeSidebar);
    sidebar.querySelectorAll("a").forEach(function (link) { link.addEventListener("click", closeSidebar); });

    var profileButton = header.querySelector(".ll-profile-button");
    var profileMenu = header.querySelector(".ll-profile-menu");
    profileButton.addEventListener("click", function (event) { event.stopPropagation(); profileMenu.hidden = !profileMenu.hidden; profileButton.setAttribute("aria-expanded", String(!profileMenu.hidden)); });
    document.addEventListener("click", function (event) { if (!header.querySelector(".ll-header-account").contains(event.target)) { profileMenu.hidden = true; profileButton.setAttribute("aria-expanded", "false"); } });
    function logout() { if (global.LLAuth && global.LLAuth.logout) global.LLAuth.logout().catch(function () {}).finally(function () { sessionStorage.removeItem("legallense_session_v1"); localStorage.removeItem("legallense_demo_session_v1"); global.location.replace("login.html"); }); }
    sidebar.querySelector(".ll-logout").addEventListener("click", logout);
    header.querySelector(".ll-menu-logout").addEventListener("click", logout);
  }

  function start() {
    if (global.__legalLensShellStarted) return;
    global.__legalLensShellStarted = true;
    var auth = global.LL && global.LL.getAuthState ? global.LL.getAuthState() : null;
    if (!auth) {
      global.location.replace("login.html");
      return;
    }
    var session = auth.session;
    var role = auth.role;
    render({ fullName: session.name || "Inspector Rajesh Kumar", email: session.email || "", demoMode: auth.demoMode }, role);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})(window);