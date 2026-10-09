import type { Component } from "solid-js";
import { For, Show, createMemo, createSignal, onCleanup } from "solid-js";
import {
  ArrowCounterClockwiseIcon,
  CheckIcon,
  CopyIcon,
  RulerIcon,
  XIcon,
} from "solid-phosphor/regular";
import { useStore } from "../../hooks/useStore.jsx";
import { duracaoLabel } from "../../lib/dwell.js";
import {
  type LngLat,
  distanciaTotalM,
  distanciasSegmentos,
  etaMs,
  haversineM,
  resolverPontosRegua,
  rotuloDistancia,
  rotuloRumo,
  rumo,
  textoCompartilhar,
} from "../../lib/regua.js";

const DURACAO_FEEDBACK_MS = 2000;

/**
 * Painel de leitura da régua: total, segmentos, rumo em linha reta, chegada
 * estimada e ações (desfazer, concluir, copiar, fechar).
 */
export const ReguaPainel: Component = () => {
  const { localState, desfazerPontoRegua, concluirRegua, desativarRegua } =
    useStore();
  const [feedbackCopia, setFeedbackCopia] = createSignal("");
  let timerFeedback: ReturnType<typeof setTimeout> | undefined;

  onCleanup(() => clearTimeout(timerFeedback));

  // Vértices com coordenadas lidas ao vivo: nós ancorados seguem o polling.
  const vertices = createMemo(() =>
    resolverPontosRegua(localState.regua.pontos, localState.nodes),
  );

  const coordenadas = createMemo(() => vertices().map((v) => v.pos as LngLat));

  const total = createMemo(() => distanciaTotalM(coordenadas()));

  const segmentos = createMemo(() => {
    const pts = coordenadas();
    const distancias = distanciasSegmentos(pts);
    return distancias.map((d, i) => ({
      de: i + 1,
      para: i + 2,
      distancia: d,
      rumo: rumo(pts[i] as LngLat, pts[i + 1] as LngLat),
    }));
  });

  const linhaReta = createMemo(() => {
    const pts = coordenadas();
    const primeiro = pts[0];
    const ultimo = pts[pts.length - 1];
    if (!(primeiro && ultimo)) {
      return null;
    }
    return {
      distancia: haversineM(primeiro, ultimo),
      rumo: rumo(primeiro, ultimo),
    };
  });

  // Chegada só quando o vértice 0 é um nó em movimento com velocidade conhecida.
  const chegada = createMemo(() => {
    const primeiroBruto = localState.regua.pontos[0];
    if (primeiroBruto?.tipo !== "no") {
      return null;
    }
    const mov = localState.movimento[primeiroBruto.nodeNum];
    if (!mov || mov.parado === true) {
      return null;
    }
    const eta = etaMs(total(), mov.velocidadeKmh);
    if (eta === null || mov.velocidadeKmh === null) {
      return null;
    }
    return { eta, velocidadeKmh: mov.velocidadeKmh };
  });

  const copiar = async () => {
    let mensagem = "Não foi possível copiar";
    try {
      if (!navigator.clipboard) {
        throw new Error("clipboard indisponível");
      }
      await navigator.clipboard.writeText(textoCompartilhar(vertices()));
      mensagem = "Copiado";
    } catch {
      // mantém a mensagem de falha
    }
    setFeedbackCopia(mensagem);
    clearTimeout(timerFeedback);
    timerFeedback = setTimeout(() => setFeedbackCopia(""), DURACAO_FEEDBACK_MS);
  };

  const btnAcao =
    "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold text-slate-200 bg-slate-800/80 hover:bg-slate-700 disabled:text-slate-500 disabled:bg-slate-900 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400";

  return (
    <section
      aria-label="Régua"
      class="pointer-events-auto absolute bottom-3 left-1/2 -translate-x-1/2 z-20 w-[min(22rem,calc(100vw-1rem))] max-w-[calc(100vw-1rem)] rounded-lg border border-slate-700/80 bg-slate-950/95 p-3 text-slate-100 shadow-2xl backdrop-blur-md"
    >
      <div class="mb-2 flex items-center gap-2 text-xs font-semibold text-amber-400">
        <RulerIcon class="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>Régua</span>
      </div>

      <Show when={vertices().length === 0}>
        <p class="text-xs text-slate-300">
          Toque no mapa ou num nó para começar
        </p>
      </Show>

      <Show when={vertices().length === 1}>
        <p class="text-xs text-slate-300">Toque no próximo ponto</p>
      </Show>

      <Show when={vertices().length >= 2}>
        <div class="flex flex-col gap-1">
          <p class="text-lg font-bold text-amber-300">
            <span data-testid="regua-total">{rotuloDistancia(total())}</span>
          </p>

          <Show when={vertices().length > 2 && linhaReta()}>
            {(reta) => (
              <p class="text-xs text-slate-300">
                Em linha reta: {rotuloDistancia(reta().distancia)} ·{" "}
                {rotuloRumo(reta().rumo)}
              </p>
            )}
          </Show>

          <ul class="flex max-h-24 flex-col gap-0.5 overflow-y-auto text-xs text-slate-300">
            <For each={segmentos()}>
              {(s) => (
                <li>
                  {s.de}→{s.para} · {rotuloDistancia(s.distancia)} ·{" "}
                  {rotuloRumo(s.rumo)}
                </li>
              )}
            </For>
          </ul>

          <Show when={chegada()}>
            {(c) => (
              <p class="text-xs text-emerald-300">
                Chegada estimada: ~{duracaoLabel(c().eta)} a{" "}
                {Math.round(c().velocidadeKmh)} km/h
              </p>
            )}
          </Show>
        </div>
      </Show>

      <div class="mt-2 flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          aria-label="Desfazer último ponto"
          class={btnAcao}
          disabled={localState.regua.pontos.length === 0}
          onClick={() => desfazerPontoRegua()}
        >
          <ArrowCounterClockwiseIcon class="h-3.5 w-3.5" aria-hidden="true" />
          <span>Desfazer</span>
        </button>

        <Show
          when={
            !localState.regua.concluida && localState.regua.pontos.length >= 2
          }
        >
          <button
            type="button"
            aria-label="Concluir medição"
            class={btnAcao}
            onClick={() => concluirRegua()}
          >
            <CheckIcon class="h-3.5 w-3.5" aria-hidden="true" />
            <span>Concluir</span>
          </button>
        </Show>

        <Show when={vertices().length >= 2}>
          <button
            type="button"
            aria-label="Copiar coordenadas e distância"
            class={btnAcao}
            onClick={() => copiar()}
          >
            <CopyIcon class="h-3.5 w-3.5" aria-hidden="true" />
            <span>Copiar</span>
          </button>
        </Show>

        <button
          type="button"
          aria-label="Fechar régua"
          class={`${btnAcao} ml-auto`}
          onClick={() => desativarRegua()}
        >
          <XIcon class="h-3.5 w-3.5" aria-hidden="true" />
          <span>Fechar</span>
        </button>
      </div>

      <Show when={feedbackCopia() !== ""}>
        <output class="mt-1.5 block text-xs text-slate-300">
          {feedbackCopia()}
        </output>
      </Show>

      <Show when={!localState.regua.concluida}>
        <p class="mt-2 text-[10px] text-slate-500">
          Duplo clique para concluir · Esc para sair
        </p>
      </Show>
    </section>
  );
};
