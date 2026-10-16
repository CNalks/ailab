// Shared motion for every page: scroll reveal from @cnalks/design-tokens (below-the-fold blocks only, so the first screen
// never flashes; nothing is hidden unless this script runs; skipped under prefers-reduced-motion) + hero scroll parallax.
import { initReveal } from "@cnalks/design-tokens/reveal";

initReveal({ selector: ".reveal, [data-reveal]" });

const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const hero = document.querySelector<HTMLElement>("[data-parallax]");
if (hero && !reduce && !window.matchMedia("(max-width: 734px)").matches) {
  let ticking = false;
  const update = () => {
    hero.style.setProperty("--p", Math.min(1, window.scrollY / (window.innerHeight || 1)).toFixed(3));
    ticking = false;
  };
  window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  update();
}
