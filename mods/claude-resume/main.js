// Setzt Claude-Sessions fort, die ein Reboot/Logout aus ihrem Terminal geworfen hat.
// Die Arbeit macht claude-resume.py (Hook + check); dieser Mod stösst den Check an,
// meldet Fortsetzungen als Toast und zeigt in der Statusleiste, wie viele Sessions er kennt.
import { View, div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { exec, notify, theme, checking, appHome, terminals } from "herdr";

const INTERVAL_MS = 10000;

export default class ClaudeResume extends View {
  init(_props, cx) {
    this.watched = 0;
    this.error = null;
    this.script = `${appHome()}/mods/claude-resume/claude-resume.py`;
    if (checking()) return;
    // Claude-Instanzen, die vor dem Hook liefen, einmal übernehmen.
    cx.spawn(async (cx) => {
      try { await exec("python3", ["-I", this.script, "adopt"], { timeout: 60000 }); } catch (_) {}
      this.check(cx);
    });
    cx.timer.every(INTERVAL_MS, (cx) => this.check(cx));
  }

  // Lokale, lebende Terminals mit gemerkter Claude-Session (Labels vom Hook).
  candidates() {
    return terminals()
      .filter((t) => t.state === "live" && t.labels?.["claude.session"] && !String(t.address).includes("/"))
      .map((t) => t.address);
  }

  check(cx) {
    if (this.running) return;
    const list = this.candidates();
    if (!list.length) {
      if (this.watched || this.error) { this.watched = 0; this.error = null; cx.notify(); }
      return;
    }
    this.running = true;
    cx.spawn(async (cx) => {
      try {
        const r = await exec("python3", ["-I", this.script, "check", ...list], { timeout: 20000 });
        const out = JSON.parse(r.stdout || "{}");
        this.error = out.error ?? (r.code ? r.stderr : null);
        this.watched = out.watched ?? 0;
        const done = (out.resumed ?? []).filter((x) => x.ok);
        const failed = (out.resumed ?? []).filter((x) => !x.ok);
        if (done.length) {
          notify({
            title: done.length === 1 ? "Claude-Session fortgesetzt" : `${done.length} Claude-Sessions fortgesetzt`,
            body: done.map((x) => x.where).join(", "),
            level: "success",
          });
        }
        if (failed.length) {
          notify({
            title: "Claude-Session nicht fortgesetzt",
            body: failed.map((x) => `${x.where}: ${x.error}`).join("\n"),
            level: "warning",
          });
        }
      } catch (e) {
        this.error = String(e);
      } finally {
        this.running = false;
      }
      cx.notify();
    });
  }

  render(cx) {
    const t = theme();
    return h_flex()
      .id("claude-resume")
      .size_full()
      .px(10)
      .gap(6)
      .items_center()
      .justify_end()
      .bg(t.chrome)
      .border_t(1)
      .border_color(t.border)
      .text_size(11.8)
      .text_color(this.error ? t.blocked : t.dim)
      .on_click((_e, cx) => this.check(cx))
      .child(div().child(this.error ? "↻ Claude: Fehler" : `↻ ${this.watched} Claude`));
  }
}
