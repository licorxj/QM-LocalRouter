// Build PNG renditions of the LocalRouter icon from its SVG sources.
// Requires sharp (npm i sharp). Run from build/icon:
//   SHARP_DIR=<dir-with-node_modules> node build_icons.mjs <variant> <outDir>
// variant: a | b | c   (renders full + compact renditions)
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
let sharp;
const sharpDir = process.env.SHARP_DIR;
if (sharpDir && existsSync(path.join(sharpDir, "node_modules", "sharp"))) {
  sharp = require(path.join(sharpDir, "node_modules", "sharp"));
} else {
  sharp = require("sharp");
}

const [variant = "a", outDir = "."] = process.argv.slice(2);
const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const src = (name) => path.join(root, `localrouter-icon-${variant}${name ? "-" + name : ""}.svg`);
const out = path.join(outDir, "");
const renditions = [
  // [svg source variant, size, file name]
  ["", 1024, `icon-${variant}-1024.png`],
  ["", 256, `icon-${variant}-256.png`],
  ["", 128, `icon-${variant}-128.png`],
  ["", 64, `icon-${variant}-64.png`],
  ["", 48, `icon-${variant}-48.png`],
  ["compact", 32, `icon-${variant}-32.png`],
  ["compact", 24, `icon-${variant}-24.png`],
  ["compact", 16, `icon-${variant}-16.png`],
];

import { mkdirSync } from "node:fs";
mkdirSync(outDir, { recursive: true });

for (const [kind, size, name] of renditions) {
  const file = src(kind);
  // Render large sizes at intrinsic 1024px; render small sizes from a 4x
  // raster so anti-aliasing survives the downscale.
  const density = size >= 256 ? 72 : 288;
  await sharp(file, { density })
    .resize(size, size)
    .png()
    .toFile(path.join(outDir, name));
  console.log(`OK ${name}`);
}
