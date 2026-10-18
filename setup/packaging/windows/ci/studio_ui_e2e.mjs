// Parcours RÉEL de création 3D par l'INTERFACE d'APEX, avec de vrais clics, contre un APEX installé par l'installateur et
// démarré par le Panel. Le navigateur (Edge sous Windows : le moteur de WebView2) ouvre l'interface comme le bouton
// « Ouvrir APEX » du Panel : un code à usage unique demandé avec le jeton de l'installation, puis `/ui/login?code=…`.
// Ce n'est pas la fenêtre d'APEX.exe elle-même, mais la même page, servie par le même APEX.
//
// 1. Paramètres > Outils : « Calcul des missions Game Studio » = Processeur, Enregistrer ;
// 2. accueil : mode 3D, la demande, « Démarrer la conversation » ; le Cerveau doit PROPOSER la mission, avec le brief
//    (« Ce que Game Studio fera de votre demande ») avant le clic ;
// 3. Atelier 3D : « Composants de Game Studio », chaque « Installer » cliqué et confirmé (boîte de dialogue acceptée) ;
// 4. « Lancer la mission » ; avec --cancel-resume : « Annuler la mission » pendant qu'elle tourne, puis « Reprendre
//    la mission » ;
// 5. suivi jusqu'à l'état final : aperçus affichés, liste des fichiers ; « Qualité de cette création » : versions,
//    portée du juge, mesures des rendus, et un avis d'essai enregistré (marqué comme tel : ce runner est jetable) ;
// 6. « Ouvrir dans Game Studio » : le projet, puis les boutons de téléchargement du .blend et du .glb ; chaque fichier
//    reçu est comparé à l'empreinte annoncée par APEX.
//
// Verdicts : PASS (0) si la mission est livrée (« review »), avec un aperçu affiché et .blend et .glb téléchargés et
// identiques à leur empreinte (et l'annulation puis la reprise vues, si demandées), le brief et le volet Qualité
// affichés ; BLOCKED (3) si un composant ne peut pas être installé ici ; FAIL (1) sinon. Preuves : <sortie>/ui/
// (captures, fichiers, ui.json).
//
// Usage : node studio_ui_e2e.mjs --out <dossier> [--base http://127.0.0.1:8000] [--home <APEX_HOME>]
//         [--channel msedge] [--cancel-resume] [--no-install] [--mission-timeout 5400]
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const REQUEST = "Crée une voiture sportive rouge avec quatre roues indépendantes et prépare son export Blender.";
const PASS = 0, FAIL = 1, BLOCKED = 3;

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const option = (name, fallback) => {
  const i = argv.indexOf(name);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : fallback;
};
const base = option("--base", "http://127.0.0.1:8000").replace(/\/$/, "");
const home = option("--home", process.env.APEX_HOME || join(process.env.LOCALAPPDATA || homedir(), "APEX"));
const out = join(option("--out", "."), "ui");
const channel = option("--channel", undefined);
const missionTimeoutMs = Number(option("--mission-timeout", "5400")) * 1000;
mkdirSync(join(out, "files"), { recursive: true });

const evidence = { phase: "studio-ui", request: REQUEST, steps: [] };
const log = (kind, text) => {
  console.log(`${kind} ${text}`);
  evidence.steps.push(`${kind} ${text}`);
};
const sha256 = (bytes) => "sha256:" + createHash("sha256").update(bytes).digest("hex");
const token = readFileSync(join(home, ".apex", "api-token"), "utf-8").trim();

async function api(method, path, body) {
  const response = await fetch(`${base}/api/v1${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(`${method} ${path} -> HTTP ${response.status} : ${JSON.stringify(payload)}`);
  return payload.data;
}

let shot = 0;
async function capture(page, name) {
  shot += 1;
  const file = `${String(shot).padStart(2, "0")}-${name}.png`;
  await page.screenshot({ path: join(out, file), fullPage: true });
  return file;
}

function finish(verdict, reason) {
  evidence.verdict = { [PASS]: "PASS", [FAIL]: "FAIL", [BLOCKED]: "BLOCKED" }[verdict];
  evidence.reason = reason;
  writeFileSync(join(out, "ui.json"), JSON.stringify(evidence, null, 2), "utf-8");
  console.log(`VERDICT ${evidence.verdict} ${reason}`);
  return verdict;
}

/** Le texte de la carte de la mission dans l'Atelier 3D, relu jusqu'à ce que `done(texte)` soit vrai. */
async function waitCard(card, done, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let text = "";
  let last = "";
  while (Date.now() < deadline) {
    text = (await card.innerText().catch(() => "")) || "";
    const state = /État : ([^\n·]+)/.exec(text)?.[1]?.trim() ?? "?";
    if (state !== last) {
      log("MISSION", `${state} ${new Date().toISOString().slice(11, 19)}`);
      last = state;
    }
    if (done(text)) return text;
    await card.page().waitForTimeout(5000);
  }
  throw new Error(`${label} : délai dépassé ; dernier état de la carte : ${text.slice(0, 400)}`);
}

async function installComponents(page, panel) {
  const details = panel.locator("details.ax-gs-dependencies");
  await details.waitFor({ timeout: 120_000 });
  if (!(await details.getAttribute("open"))) await details.locator("summary").click();
  log("NOTE", `composants : ${(await details.locator("summary").innerText()).trim()}`);
  for (let round = 0; round < 4; round += 1) {
    const button = details.getByRole("button", { name: "Installer" }).first();
    if (!(await button.count())) break;
    const item = (await button.locator("xpath=ancestor::li[1]").locator("strong").innerText()).trim();
    await button.click();
    log("CLICK", `Installer « ${item} » (confirmation acceptée)`);
    const status = details.getByRole("status");
    const deadline = Date.now() + 3_600_000;
    let text = "";
    while (Date.now() < deadline) {
      text = ((await status.innerText().catch(() => "")) || "").trim();
      // Le statut nomme le composant de l'installation en cours : celui d'avant reste affiché un instant.
      if (text.includes(item) && !text.includes("en cours")) break;
      await page.waitForTimeout(5000);
    }
    log("CHECK", `install ${item} : ${text.slice(0, 300)}`);
    await capture(page, `composant-${round + 1}`);
    if (!/installé/.test(text) || /échouée/.test(text)) return `installation de « ${item} » : ${text}`;
    // La liste se relit seule après l'installation ; on attend que le bouton suivant (s'il existe) apparaisse.
    await page.waitForTimeout(8000);
  }
  const summary = (await details.locator("summary").innerText()).trim();
  log("NOTE", `composants après installation : ${summary}`);
  if (!summary.includes("tous présents")) {
    const missing = await details.locator("li.ax-gs-dependency[data-state]:not([data-state=present]) strong").allInnerTexts();
    return `composants encore absents : ${missing.join(", ")}`;
  }
  return null;
}

async function main() {
  const { login_path: loginPath } = await api("POST", "/ui/launch-code");
  const browser = await chromium.launch({ channel, headless: true });
  const context = await browser.newContext({ acceptDownloads: true, locale: "fr-FR", viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on("dialog", async (dialog) => {
    log("DIALOG", dialog.message().slice(0, 300));
    await dialog.accept();
  });
  page.setDefaultTimeout(60_000);
  try {
    await page.goto(base + loginPath);
    await page.waitForURL((url) => !url.pathname.startsWith("/ui/login"));
    await capture(page, "accueil");

    // 1. Paramètres > Outils : le calcul des missions sur le processeur, choix explicite.
    await page.goto(`${base}/settings`);
    const compute = page.getByLabel("Calcul des missions Game Studio");
    await compute.selectOption("cpu");
    const save = compute.locator("xpath=ancestor::section[1]").getByRole("button", { name: "Enregistrer" });
    // Grisé quand rien n'a changé (réglage déjà sur Processeur).
    if (await save.isEnabled()) {
      await save.click();
      log("CLICK", "Paramètres > Outils : Processeur, Enregistrer");
      await page.waitForTimeout(2000);
    }
    const settings = await api("GET", "/assistant/settings");
    log("CHECK", `calcul des missions enregistré : ${settings.tools.game_studio_compute}`);
    if (settings.tools.game_studio_compute !== "cpu") return finish(FAIL, "le réglage Processeur n'a pas été enregistré");

    // 2. Accueil : mode 3D, la demande, puis la conversation.
    await page.goto(`${base}/`);
    await page.getByRole("radio", { name: /3D/ }).click();
    await page.getByLabel("Message").fill(REQUEST);
    await page.getByRole("button", { name: "Démarrer la conversation" }).click();
    await page.waitForURL(/\/c\/[^/]+$/, { timeout: 120_000 });
    const conversationId = new URL(page.url()).pathname.split("/").pop();
    evidence.conversation_id = conversationId;
    log("NOTE", `conversation ${conversationId}`);
    const panel = page.getByRole("complementary", { name: "Vue du mode" });
    const proposal = panel.locator("li.ax-gs-mission", { hasText: "Mission proposée" });
    try {
      await proposal.waitFor({ timeout: 1_800_000 });
    } catch {
      await capture(page, "sans-proposition");
      return finish(FAIL, "aucune mission proposée par le Cerveau pour une demande de création claire");
    }
    log("CHECK", `proposition affichée : ${(await proposal.innerText()).replace(/\s+/g, " ").slice(0, 300)}`);
    evidence.brief_problem = await checkBrief(proposal);
    await capture(page, "proposition");

    // 3. Composants de Game Studio, installés par leurs boutons.
    if (!flag("--no-install")) {
      const missing = await installComponents(page, panel);
      if (missing) return finish(BLOCKED, missing);
      await page.reload();
    }

    // 4. Le clic de l'utilisateur.
    await panel.getByRole("button", { name: "Lancer la mission" }).click();
    log("CLICK", "Lancer la mission");
    const card = panel.locator("li.ax-gs-mission", { has: page.getByRole("heading", { name: /^Mission [0-9a-f]{8}$/ }) });
    try {
      await card.waitFor({ timeout: 120_000 });
    } catch {
      // Un refus de Game Studio s'affiche dans l'Atelier 3D (« Game Studio a refusé la mission : … »).
      const refusal = [...await panel.locator("li", { hasText: "Game Studio a refusé la mission" }).allInnerTexts(),
        ...await panel.getByRole("alert").allInnerTexts()].map((t) => t.replace(/\s+/g, " ").trim());
      await capture(page, "lancement-refuse");
      return finish(flag("--no-install") ? BLOCKED : FAIL, `mission non lancée : ${refusal.join(" ; ") || "aucune carte de mission"}`);
    }
    await capture(page, "mission-lancee");

    if (flag("--cancel-resume")) {
      await waitCard(card, (t) => t.includes("État : en cours"), 300_000, "démarrage");
      await page.waitForTimeout(20_000);
      await card.getByRole("button", { name: "Annuler la mission" }).click();
      log("CLICK", "Annuler la mission");
      await waitCard(card, (t) => t.includes("État : annulée"), 300_000, "annulation");
      evidence.cancelled = true;
      await capture(page, "mission-annulee");
      await card.getByRole("button", { name: "Reprendre la mission" }).click();
      log("CLICK", "Reprendre la mission");
      await waitCard(card, (t) => t.includes("État : en cours") || t.includes("État : terminée"), 300_000, "reprise");
      evidence.resumed = true;
    }

    // 5. Suivi jusqu'à l'état final, aperçus et fichiers.
    const final = await waitCard(card, (t) => /État : (terminée|échouée|annulée|interrompue)/.test(t), missionTimeoutMs, "mission");
    evidence.card = final;
    await page.waitForTimeout(5000);
    const previews = await card.locator("img.ax-model3d__preview").evaluateAll(
      (images) => images.filter((img) => img.complete && img.naturalWidth > 0).length);
    log("CHECK", `aperçus affichés : ${previews}`);
    await capture(page, "mission-finale");
    if (!final.includes("État : terminée")) {
      const reason = /Raison : ([^\n]+)/.exec(final)?.[1] ?? "aucune raison affichée";
      return finish(FAIL, `mission non livrée : ${reason}`);
    }

    // 6. Téléchargements par la page Game Studio, comparés aux empreintes annoncées par APEX.
    const prefix = (await card.getByRole("heading").innerText()).replace("Mission ", "").trim();
    const projectId = await fullProjectId(conversationId, prefix);
    evidence.project_id = projectId;
    // Une erreur du volet Qualité ne doit pas empêcher de vérifier les téléchargements : elle est rapportée.
    evidence.quality_problem = await checkQuality(page, card, conversationId, projectId)
      .catch((error) => `volet Qualité : ${error.name}: ${error.message}`.slice(0, 600));
    const announced = (await api("GET", `/assistant/conversations/${conversationId}/game-studio/${projectId}`)).results.artifacts ?? [];
    await card.getByRole("link", { name: "Ouvrir dans Game Studio" }).click();
    await page.waitForURL(/\/game-studio/);
    const projects = page.locator("section", { has: page.getByRole("heading", { name: "Projets multi-assets" }) });
    await projects.getByRole("button").first().click();
    const followup = page.getByRole("region", { name: "Suivi du projet" });
    await followup.waitFor({ timeout: 60_000 });
    // La version retenue par le juge : l'article de son titre (« Version n · retenue »), pas celui de l'étape.
    const retained = followup.locator("h4", { hasText: "· retenue" }).first().locator("xpath=..");
    await capture(page, "game-studio");
    const downloads = [];
    for (const name of ["asset.blend", "asset.glb"]) {
      const button = retained.getByRole("button", { name, exact: true });
      const [download] = await Promise.all([page.waitForEvent("download", { timeout: 120_000 }), button.click()]);
      const target = join(out, "files", name);
      await download.saveAs(target);
      const bytes = readFileSync(target);
      const expected = announced.find((a) => a.name === name)?.hash;
      const verified = expected === sha256(bytes);
      downloads.push({ name, bytes: bytes.length, verified });
      log("CHECK", `téléchargé par le bouton « ${name} » : ${bytes.length} octets, empreinte ${verified ? "vérifiée" : `FAUSSE (${expected})`}`);
    }
    evidence.downloads = downloads;
    evidence.previews = previews;
    const cancelled = !flag("--cancel-resume") || (evidence.cancelled && evidence.resumed);
    const display = [evidence.brief_problem, evidence.quality_problem].filter(Boolean);
    if (previews > 0 && downloads.every((d) => d.verified) && cancelled && display.length === 0) {
      return finish(PASS, `mission livrée par l'interface : ${previews} aperçu(s), .blend et .glb téléchargés et vérifiés`
        + (flag("--cancel-resume") ? ", annulée puis reprise" : "") + ", brief et volet Qualité affichés");
    }
    return finish(FAIL, `livraison incomplète : aperçus ${previews}, téléchargements ${JSON.stringify(downloads)}`
      + (display.length ? ` ; ${display.join(" ; ")}` : ""));
  } catch (error) {
    await capture(page, "erreur").catch(() => undefined);
    return finish(FAIL, `${error.name}: ${error.message}`.slice(0, 1200));
  } finally {
    await browser.close();
  }
}

/** Le brief joint à la proposition : chaque élément de la demande et son sort, d'après l'extracteur de Game Studio.
 *  Rend `null` s'il est affiché comme attendu, sinon le problème (la mission continue : il ne la change pas). */
async function checkBrief(proposal) {
  const brief = proposal.locator("details.ax-gs-brief");
  if (!(await brief.count())) return "brief absent de la proposition";
  if ((await brief.getAttribute("open")) === null) await brief.locator("summary").click();
  const items = await brief.locator("li[data-status]").evaluateAll(
    (lis) => lis.map((li) => `${li.dataset.status} ${li.innerText.replace(/\s+/g, " ").trim()}`));
  evidence.brief = items;
  for (const item of items) log("BRIEF", item.slice(0, 240));
  if (items.some((item) => item.startsWith("refused"))) return "le brief annonce un refus pour une demande qui passe";
  if (!items.some((item) => item.startsWith("verified"))) return "aucun élément du brief vérifié par Game Studio";
  return null;
}

/** « Qualité de cette création » sur la carte livrée : versions et verdicts, portée du juge, mesures des rendus de
 *  la version retenue ; puis un avis d'ESSAI, enregistré par le formulaire et relu par l'API. Rend `null` ou le
 *  problème. */
async function checkQuality(page, card, conversationId, projectId) {
  const details = card.locator("details.ax-gs-quality");
  if (!(await details.count())) return "volet Qualité absent de la mission livrée";
  await details.locator("summary").click();
  const form = details.locator("form.ax-gs-rating");
  try {
    await form.waitFor({ timeout: 300_000 });
  } catch {
    return `volet Qualité non chargé : ${(await details.innerText()).replace(/\s+/g, " ").slice(0, 300)}`;
  }
  const lines = (await details.locator("li").allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim());
  for (const line of lines) log("QUALITY", line.slice(0, 400));
  const judge = (await details.locator("p", { hasText: "Le juge de Game Studio vérifie" }).allInnerTexts()).join(" ");
  log("QUALITY", judge.replace(/\s+/g, " ").slice(0, 400));
  await capture(page, "qualite");
  const report = await api("GET", `/assistant/conversations/${conversationId}/game-studio/${projectId}/quality`);
  evidence.quality = report;
  const problems = [];
  if (!lines.some((line) => line.startsWith("Version "))) problems.push("aucune version affichée");
  if (!judge.includes("Il ne mesure ni la beauté")) problems.push("portée du juge non affichée");
  if (report.metrics_available && !lines.some((line) => line.includes("couleur perçue"))) {
    problems.push("mesures des rendus absentes");
  }
  if (!report.metrics_available) log("NOTE", "mesures indisponibles dans cette installation (Pillow ou numpy absent)");

  // L'avis d'essai : ce runner est détruit à la fin du job ; ce n'est pas une donnée de calibration.
  const before = report.ratings.length;
  await details.getByLabel("réalisme des matériaux").first().selectOption("3");
  await details.getByLabel("Commentaire").fill("Essai automatique du runner Windows : pas un avis humain.");
  await details.getByRole("button", { name: "Enregistrer mon avis" }).click();
  log("CLICK", "Enregistrer mon avis (essai, pas un avis humain)");
  try {
    await details.getByRole("status").filter({ hasText: "Avis enregistré" }).waitFor({ timeout: 60_000 });
  } catch {
    problems.push(`avis non enregistré : ${(await details.getByRole("alert").allInnerTexts()).join(" ")}`);
  }
  const after = (await api("GET", `/assistant/conversations/${conversationId}/game-studio/${projectId}/quality`)).ratings;
  const saved = after.length === before + 1 && after.at(-1)?.scores?.materiaux === 3;
  log("CHECK", `avis relu par l'API : ${saved ? "oui" : "non"} (${after.length} avis) ; fichier `
    + join(home, ".apex", "game-studio-ratings.jsonl"));
  if (!saved) problems.push("avis non relu par l'API");
  return problems.length ? `volet Qualité : ${problems.join(", ")}` : null;
}

/** L'identifiant complet de la mission dont la carte montre les 8 premiers caractères (le suivi de l'assistant). */
async function fullProjectId(conversationId, prefix) {
  const messages = await api("GET", `/assistant/conversations/${conversationId}/messages`);
  const ids = JSON.stringify(messages).match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g) ?? [];
  const id = ids.find((value) => value.startsWith(prefix));
  if (!id) throw new Error(`mission ${prefix} introuvable dans la conversation`);
  return id;
}

process.exitCode = await main().catch((error) => {
  console.log(`FAIL ${error.name}: ${error.message}`);
  return FAIL;
});
