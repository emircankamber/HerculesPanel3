// Panelin Tailwind yapılandırması — eskiden index.html'deki CDN (cdn.tailwindcss.com) içinde
// satır içi `tailwind.config` idi. Derlenmiş çıktı: /tailwind.css (repoya eklenir).
// Yeniden derleme: scripts/build-css.sh — index.html / app.js'e yeni bir Tailwind sınıfı
// eklediğinde ÇALIŞTIR, yoksa sınıf CSS'te olmaz ve görünmez.
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./app.js"],
  // app.js'te parça birleştirilerek üretilen (taramada görünmeyen) Tailwind sınıfları buraya.
  // Şu an yok: `${...}` ile kurulan sınıfların hepsi styles.css'teki özel sınıflar
  // (chip/lv-N/ck-*/…) ya da ternary içinde TAM yazılmış literaller (tarama bunları görür).
  // Yeni bir `bg-${renk}` gibi kalıp eklersen olası tüm sonuçlarını buraya yaz.
  safelist: [],
  theme: { extend: {
    // "Editorial Intelligence" tasarım token'ları (stitch referans tasarımıyla aynı)
    colors: {
      "primary": "#005c55", "primary-container": "#0f766e", "primary-fixed": "#9cf2e8",
      "on-primary": "#ffffff", "secondary": "#565e74", "secondary-container": "#dae2fd",
      "tertiary": "#7d4200", "tertiary-container": "#a15600",
      "error": "#ba1a1a", "error-container": "#ffdad6",
      "surface": "#f8f9ff", "surface-container-lowest": "#ffffff", "surface-container-low": "#eff4ff",
      "surface-container": "#e5eeff", "surface-container-high": "#dce9ff",
      "on-surface": "#0b1c30", "on-surface-variant": "#3e4947",
      "outline": "#6e7977", "outline-variant": "#bdc9c6", "hairline": "#e3e9f2",
    },
    fontFamily: { display: ["Manrope", "Inter", "sans-serif"], sans: ["Inter", "system-ui", "sans-serif"] },
  } },
};
