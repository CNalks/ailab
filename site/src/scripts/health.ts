// Health badge: the only runtime request. Same-origin GET /ailab-api/health (read-only, public); any failure => offline text.
const els = document.querySelectorAll<HTMLElement>("[data-health]");
if (els.length) {
  const first = els[0];
  const msgs = JSON.parse(first.dataset.msgs ?? "{}") as Record<string, string>;
  const set = (state: string, msg: string) => {
    els.forEach((el) => {
      el.dataset.state = state;
      const text = el.querySelector<HTMLElement>("[data-health-text]");
      if (text) text.textContent = msg;
    });
  };
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 4000);
  fetch("/ailab-api/health", { cache: "no-store", signal: ctl.signal, headers: { accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((j: { status?: string; version?: string }) => {
      if (j && j.status === "ok") set("online", (msgs.online ?? "online").replace("{version}", String(j.version ?? "")));
      else set("offline", msgs.offline ?? "offline");
    })
    .catch(() => set("offline", msgs.offline ?? "offline"))
    .finally(() => clearTimeout(timer));
}
