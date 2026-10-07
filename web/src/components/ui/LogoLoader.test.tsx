import { cleanup, render, screen } from "@solidjs/testing-library";
import { afterEach, describe, expect, it } from "vitest";
import { LogoLoader } from "./LogoLoader.jsx";

describe("LogoLoader", () => {
  afterEach(() => {
    cleanup();
  });

  it("renderiza o container com atributos de acessibilidade e classes de animação", () => {
    const { container } = render(() => <LogoLoader />);
    const loader = screen.getByTestId("logo-loader");
    expect(loader).not.toBeNull();
    expect(loader.getAttribute("role")).toBe("status");
    expect(loader.getAttribute("aria-busy")).toBe("true");

    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();

    const nodePath = container.querySelector(".rastro-logo-node");
    expect(nodePath).not.toBeNull();

    const outerWave = container.querySelector(".rastro-logo-mesh-outer");
    expect(outerWave).not.toBeNull();
  });

  it("renderiza o texto opcional de status quando informado", () => {
    render(() => <LogoLoader text="Conectando à malha Rastro..." />);
    expect(screen.getByText("Conectando à malha Rastro...")).not.toBeNull();
  });

  it("não renderiza texto quando a prop text é omitida", () => {
    const { container } = render(() => <LogoLoader />);
    const textEl = container.querySelector("p");
    expect(textEl).toBeNull();
  });

  it("aplica classes de dimensão corretas para sm, md e lg", () => {
    const { container: cSm } = render(() => <LogoLoader size="sm" />);
    expect(cSm.querySelector(".w-10")).not.toBeNull();
    cleanup();

    const { container: cMd } = render(() => <LogoLoader size="md" />);
    expect(cMd.querySelector(".w-20")).not.toBeNull();
    cleanup();

    const { container: cLg } = render(() => <LogoLoader size="lg" />);
    expect(cLg.querySelector(".w-32")).not.toBeNull();
  });

  it("aplica classe CSS adicional quando fornecida", () => {
    render(() => <LogoLoader class="my-custom-loader" />);
    const loader = screen.getByTestId("logo-loader");
    expect(loader.classList.contains("my-custom-loader")).toBe(true);
  });
});
