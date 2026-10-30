"""Teste automatizado ponta a ponta do pré-piloto (Playwright + Chromium, microfone falso).

Sessão A (P90): boas-vindas, checagem, teste de microfone, prática (escrita + áudio),
12 trials, 1 checkpoint, troca de aba simulada, saída e volta da tela cheia, encerramento
pelo botão de cansaço, questionário e download do .zip.

Sessão B (P91): 3 trials e fechamento da janela no meio; reabre o link e baixa os trials
concluídos pela tela de recuperação.

Uso (com o servidor local rodando: python -m http.server 8765):
    python tests/e2e_test.py --url http://127.0.0.1:8765 --out tests/output
Depois rode a verificação:
    python tests/verify_zip.py tests/output
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

CAPTURE_LOGS = """
window.__pilotLogs = [];
for (const k of ['error', 'warn']) {
  const orig = console[k];
  console[k] = (...a) => { window.__pilotLogs.push(k + ': ' + a.map(String).join(' ')); orig.apply(console, a); };
}
window.addEventListener('error', (e) => window.__pilotLogs.push('onerror: ' + e.message));
window.addEventListener('unhandledrejection', (e) => window.__pilotLogs.push('unhandled: ' + (e.reason && e.reason.stack || e.reason)));
"""

SIMULATE_TAB_SWITCH = """() => {
  window.__realHasFocus = window.__realHasFocus || document.hasFocus.bind(document);
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
  document.hasFocus = () => false;
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('blur'));
}"""
SIMULATE_TAB_RETURN = """() => {
  delete document.visibilityState;
  delete document.hidden;
  document.hasFocus = window.__realHasFocus;
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
}"""

ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


async def dump(page, tag):
    """Em caso de falha, salva screenshot e texto da tela para diagnóstico."""
    out = Path(__file__).parent / "output"
    try:
        await page.screenshot(path=str(out / f"fail_{tag}.png"))
        txt = await page.evaluate("() => document.body.innerText")
        st = await page.evaluate("() => window.__pilot && JSON.stringify({cur: window.__pilot.state.cur, stop: window.__pilot.state.stop, rec: window.__pilot.state.recorder && window.__pilot.state.recorder.state, logs: window.__pilotLogs || []})")
        (out / f"fail_{tag}.txt").write_text(txt + "\n\n" + str(st))
    except Exception as e:  # noqa
        print("dump falhou", e)


async def click_button(page, name, timeout=30000):
    try:
        await _click_button(page, name, timeout)
    except Exception:
        await dump(page, "click_" + name.replace(" ", "_")[:20])
        raise


async def _click_button(page, name, timeout=30000):
    btn = page.get_by_role("button", name=name, exact=True)
    await btn.wait_for(state="visible", timeout=timeout)
    await page.wait_for_function(
        "(n) => [...document.querySelectorAll('button, input[type=submit]')].some(b => (b.value && b.tagName === 'INPUT' ? b.value : b.textContent).trim() === n && !b.disabled && b.offsetParent)",
        arg=name, timeout=timeout)
    await btn.click()


async def question_contains(page, text, timeout=30000):
    try:
        await _question_contains(page, text, timeout)
    except Exception:
        await dump(page, "question_" + text[:10])
        raise


async def _question_contains(page, text, timeout=30000):
    await page.wait_for_function(
        "(t) => { const q = document.querySelector('.question'); return q && q.textContent.includes(t); }",
        arg=text, timeout=timeout)


async def cur(page):
    return await page.evaluate("() => window.__pilot.state.cur && Object.assign({}, window.__pilot.state.cur)")


async def start_session(page, url):
    await page.goto(url)
    await page.wait_for_selector("text=IAmagética")
    await click_button(page, "Começar")
    log("boas-vindas ok")
    await click_button(page, "Continuar", timeout=60000)  # requisitos
    log("requisitos ok")
    await click_button(page, "Gravar 3 segundos")
    await click_button(page, "Ouvi minha voz, continuar", timeout=20000)
    log("teste de microfone ok")
    for _ in range(4):
        await click_button(page, "Continuar")
    await click_button(page, "Entrar em tela cheia")
    await page.wait_for_timeout(1300)
    fs = await page.evaluate("() => !!document.fullscreenElement")
    log("tela cheia:", fs)
    await click_button(page, "Começar a prática")


async def do_trial(page, opts=None):
    """Responde um trial a partir da tela de vividez. Devolve o registro do trial em andamento."""
    opts = opts or {}
    await question_contains(page, "vívida", timeout=40000)
    c = await cur(page)
    # vividez pelo teclado, dinamismo com clique
    await page.keyboard.press(str(opts.get("vividness", 4)))
    await question_contains(page, "dinâmica")
    if opts.get("before_dynamism"):
        await opts["before_dynamism"]()
    await page.locator(".scale-btn").nth(opts.get("dynamism", 2) - 1).click()
    await question_contains(page, "já viu")
    # reconhecimento sempre correto, pelo teclado (S/N)
    await page.keyboard.press("s" if c["is_repeat"] else "n")
    await question_contains(page, "Descreva")
    if c["description_mode"] == "written":
        ta = page.locator("#desc-text")
        await ta.click()
        # números digitados na caixa de texto não podem acionar atalhos
        await page.keyboard.type(f"teste 1 2 3 S N trial {c['trial_index']} {c['stim_id']}")
        await page.wait_for_timeout(200)
        still = await page.locator("#desc-text").count()
        assert still == 1, "atalho de teclado disparou dentro da caixa de texto"
        await click_button(page, "Continuar")
    else:
        # "fala" simulada (o microfone falso gera um tom) antes de parar
        await page.wait_for_selector("#finish-trial", timeout=20000)
        await page.wait_for_timeout(opts.get("speak_ms", 1500))
        await click_button(page, "Parar gravação")
        if opts.get("rerecord"):
            await click_button(page, "Gravar de novo")
            await page.wait_for_selector("#finish-trial", timeout=20000)
            await page.wait_for_timeout(1200)
            await click_button(page, "Parar gravação")
        await click_button(page, "Continuar")
    return c


async def session_a(p, base, out):
    ctx = await p.chromium.launch_persistent_context(
        str(out / "profile_a"), headless=True, args=ARGS, accept_downloads=True,
        viewport={"width": 1280, "height": 860}, permissions=["microphone"])
    await ctx.add_init_script(CAPTURE_LOGS)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

    tab_switch_mode = None
    await start_session(page, f"{base}/?pid=P90&seed=12345")
    # prática: 1 escrita + 1 áudio (este com regravação)
    for k in range(2):
        c = await do_trial(page, {"rerecord": k == 1})
        log(f"prática {k + 1}: {c['stim_id']} ({c['description_mode']})")
    await click_button(page, "Começar")

    for i in range(1, 13):
        opts = {"vividness": 1 + (i % 5), "dynamism": 1 + ((i + 2) % 5)}
        if i == 3:
            # troca de aba simulada durante a fase de imaginação
            await page.wait_for_function("() => window.__pilot.state.cur && window.__pilot.state.cur.trial_index === 3")
            await page.wait_for_selector(".imagery-text", timeout=20000)
            other = await ctx.new_page()
            await other.goto("about:blank")
            await other.bring_to_front()
            await page.wait_for_timeout(1500)
            await other.close()
            await page.bring_to_front()
            n = await page.evaluate("() => window.__pilot.state.cur.n_focus_losses")
            tab_switch_mode = "real"
            if not n:
                # No Chromium headless a página continua "visível" e com foco mesmo com outra aba
                # na frente. Então simulamos o que o Chrome faz numa troca de aba real:
                # visibilityState=hidden + visibilitychange + blur, e depois a volta.
                tab_switch_mode = "synthetic"
                await page.evaluate(SIMULATE_TAB_SWITCH)
                await page.wait_for_timeout(1500)
                await page.evaluate(SIMULATE_TAB_RETURN)
            log(f"troca de aba simulada no trial 3 ({tab_switch_mode})")
        if i == 5:
            async def leave_fs():
                await page.evaluate("() => document.exitFullscreen()")
                await page.wait_for_selector("#fs-overlay:not([hidden])", timeout=5000)
                await page.wait_for_timeout(700)
                await click_button(page, "Voltar para a tela cheia")
                await page.wait_for_selector("#fs-overlay[hidden]", state="attached", timeout=5000)
                log("saída e volta da tela cheia no trial 5")
            opts["before_dynamism"] = leave_fs
        c = await do_trial(page, opts)
        log(f"trial {c['trial_index']}: {c['stim_id']} {c['stim_type']} rep={c['is_repeat']} {c['description_mode']}")
        if i == 10:
            await page.wait_for_selector("input[name=fatigue]", timeout=20000)
            await page.locator("input[name=fatigue][value='2']").check()
            await page.locator("input[name=concentration][value='4']").check()
            await click_button(page, "Continuar")
            log("checkpoint ok")

    # trial 13: encerra pelo botão de cansaço na tela de vividez
    await question_contains(page, "vívida", timeout=40000)
    await page.locator("#fatigue-btn").click()
    await click_button(page, "Sim, encerrar")
    log("encerrado pelo botão de cansaço")

    # questionário
    await page.wait_for_selector("input[name=stop_reason]", timeout=20000)
    await page.locator("input[name=stop_reason][value=cansaco]").check()
    for name, v in [("instructions_clarity", 4), ("vividness_definition_sense", 5), ("dynamism_question_clarity", 3),
                    ("mask_annoyance", 2), ("recognition_difficulty", 3)]:
        await page.locator(f"input[name={name}][value='{v}']").check()
    await page.locator("input[name=imagery_time][value=ok]").check()
    await page.locator("input[name=stim_mask_time][value=ok]").check()
    await page.locator("input[name=description_preference][value=escrita]").check()
    await page.locator("textarea[name=description_preference_why]").fill("Mais rápido de revisar.")
    await page.locator("textarea[name=fatigue_onset]").fill("Por volta do trial 10.")
    await page.locator("input[name=feasible_n_stimuli]").fill("60")
    await page.locator("input[name=interruptions][value=nao]").check()
    await page.locator("textarea[name=comments]").fill("Teste automatizado, com vírgula, \"aspas\" e\nquebra de linha.")
    async with page.expect_download(timeout=60000) as dl_info:
        await click_button(page, "Enviar respostas")
    dl = await dl_info.value
    target = out / dl.suggested_filename
    await dl.save_as(target)
    log("download:", target.name, f"{target.stat().st_size / 1024:.1f} KB")

    await page.wait_for_selector("#copy-email", timeout=20000)
    final_text = await page.inner_text("#final-card")
    mailto = await page.get_attribute("#mailto-link", "href")
    async with page.expect_download(timeout=30000) as dl2:
        await click_button(page, "Baixar de novo")
    d2 = await dl2.value
    log("baixar de novo ok:", d2.suggested_filename)
    fs_after = await page.evaluate("() => !!document.fullscreenElement")
    await ctx.close()
    return {"tab_switch_mode": tab_switch_mode, "zip": str(target), "final_text": final_text, "mailto": mailto, "errors": errors,
            "redownload_name": d2.suggested_filename, "fullscreen_after_end": fs_after}


async def session_b(p, base, out):
    """Interrompe a sessão (fecha a janela) e confere a recuperação."""
    profile = out / "profile_b"
    ctx = await p.chromium.launch_persistent_context(
        str(profile), headless=True, args=ARGS, accept_downloads=True,
        viewport={"width": 1280, "height": 860}, permissions=["microphone"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await start_session(page, f"{base}/?pid=P91&seed=777")
    for k in range(2):
        await do_trial(page, {})
    await click_button(page, "Começar")
    for i in range(3):
        c = await do_trial(page, {})
        log(f"[B] trial {c['trial_index']} ok")
    # espera o trial 4 começar e fecha tudo no meio dele
    await page.wait_for_selector(".imagery-text", timeout=30000)
    await page.wait_for_timeout(500)  # garante que a gravação do trial 3 terminou
    await ctx.close()
    log("[B] janela fechada no meio do trial 4")

    ctx = await p.chromium.launch_persistent_context(
        str(profile), headless=True, args=ARGS, accept_downloads=True,
        viewport={"width": 1280, "height": 860}, permissions=["microphone"])
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(f"{base}/?pid=P91")
    await page.wait_for_selector(".recovery-list", timeout=20000)
    recovery_text = await page.inner_text("#gate")
    new_disabled = await page.locator("#new-session").is_disabled()
    async with page.expect_download(timeout=60000) as dl_info:
        await click_button(page, "Baixar os dados da sessão anterior")
    dl = await dl_info.value
    target = out / ("RECOVERED_" + dl.suggested_filename)
    await dl.save_as(target)
    await page.wait_for_function("() => !document.querySelector('#new-session').disabled", timeout=10000)
    log("[B] recuperação baixada:", target.name)
    # ao reabrir de novo, não deve mais oferecer a recuperação (já baixada)
    await page.goto(f"{base}/?pid=P91")
    await page.wait_for_timeout(1500)
    shows_recovery_again = await page.locator(".recovery-list").count() > 0
    await ctx.close()
    return {"zip": str(target), "recovery_text": recovery_text, "new_session_disabled_before": new_disabled,
            "shows_recovery_again": shows_recovery_again}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--out", default=str(Path(__file__).parent / "output"))
    ap.add_argument("--only", choices=["a", "b"], default=None)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.zip"):
        old.unlink()
    import shutil
    for prof in ("profile_a", "profile_b"):
        shutil.rmtree(out / prof, ignore_errors=True)

    res = {}
    t0 = time.time()
    async with async_playwright() as p:
        if args.only in (None, "a"):
            res["a"] = await session_a(p, args.url, out)
        if args.only in (None, "b"):
            res["b"] = await session_b(p, args.url, out)
    res["elapsed_s"] = round(time.time() - t0)
    (out / "e2e_result.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    log("resultado salvo em", out / "e2e_result.json")


if __name__ == "__main__":
    asyncio.run(main())
