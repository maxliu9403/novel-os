import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type ModelPricing, type StudioPricing } from "../api/client";
import PricingSettings from "./PricingSettings";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Unexpected network request")));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function pricing(models?: ModelPricing[]): StudioPricing {
  return {schema_version: 1, usd_to_cny: 7.2, exchange_rate_status: "assumed", models: models ?? [{
    provider: "codex", model: "text-fixture", input_usd_per_million: 2,
    cached_input_usd_per_million: null, output_usd_per_million: 8, image_usd_per_image: null,
  }]};
}
function setup(value = pricing()) {
  const read = vi.spyOn(api, "studioPricing").mockResolvedValue(value);
  const save = vi.spyOn(api, "updateStudioPricing").mockImplementation(async body => body);
  render(<PricingSettings />);
  return {read, save, user: userEvent.setup()};
}

test("saving exchange rate preserves null unconfigured prices separately from explicit free zero", async () => {
  const {save, user} = setup();
  const exchange = await screen.findByLabelText("1 美元兑换人民币");
  await user.clear(exchange); await user.type(exchange, "7.35");
  await user.clear(screen.getByLabelText("输入 / 百万 Token 1"));
  await user.type(screen.getByLabelText("缓存输入 / 百万 Token 1"), "0");
  await user.type(screen.getByLabelText("每张图片 1"), "0.04");
  await user.clear(screen.getByLabelText("模型 1")); await user.type(screen.getByLabelText("模型 1"), "  text-fixture-v2  ");
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledExactlyOnceWith({schema_version: 1, usd_to_cny: 7.35, exchange_rate_status: "configured", models: [{
    provider: "codex", model: "text-fixture-v2", input_usd_per_million: null,
    cached_input_usd_per_million: 0, output_usd_per_million: 8, image_usd_per_image: 0.04,
  }]});
  expect(screen.getByLabelText("输入 / 百万 Token 1")).toHaveValue(null);
  expect(screen.getByLabelText("缓存输入 / 百万 Token 1")).toHaveValue(0);
  expect(screen.getByLabelText("模型 1")).toHaveValue("text-fixture-v2");
  expect(fetch).not.toHaveBeenCalled();
});

test("clearing the exchange rate explicitly saves unset instead of zero", async () => {
  const {save, user} = setup();
  await user.clear(await screen.findByLabelText("1 美元兑换人民币"));
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledWith(expect.objectContaining({usd_to_cny: null, exchange_rate_status: "unset"}));
});

test("duplicate provider and model identities are rejected after whitespace normalization", async () => {
  const {save, user} = setup();
  await user.click(await screen.findByRole("button", {name: "添加模型单价"}));
  await user.type(screen.getByLabelText("模型 2"), " text-fixture ");
  await user.clear(screen.getByLabelText("服务商 2"));
  await user.type(screen.getByLabelText("服务商 2"), " codex ");
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("同一服务商和模型只能设置一组单价");
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByLabelText("模型 2")).toHaveValue(" text-fixture ");
  await user.click(screen.getByRole("button", {name: "移除模型单价 2"}));
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledTimes(1);
});

test("same model IDs from distinct providers keep independent prices", async () => {
  const {save, user} = setup();
  await user.click(await screen.findByRole("button", {name: "添加模型单价"}));
  await user.type(screen.getByLabelText("模型 2"), "text-fixture");
  await user.clear(screen.getByLabelText("服务商 2")); await user.type(screen.getByLabelText("服务商 2"), "openai");
  await user.type(screen.getByLabelText("输入 / 百万 Token 2"), "0");
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledWith(expect.objectContaining({models: [pricing().models[0], {
    provider: "openai", model: "text-fixture", input_usd_per_million: 0,
    cached_input_usd_per_million: null, output_usd_per_million: null, image_usd_per_image: null,
  }]}));
});

test("save failure keeps edited prices and exchange rate for an identical retry", async () => {
  const {save, user, read} = setup();
  save.mockRejectedValueOnce(new Error("设置保存冲突，请重试"));
  const input = await screen.findByLabelText("输出 / 百万 Token 1");
  await user.clear(input); await user.type(input, "9.75");
  await user.clear(screen.getByLabelText("1 美元兑换人民币")); await user.type(screen.getByLabelText("1 美元兑换人民币"), "7.1");
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("设置保存冲突，请重试");
  expect(input).toHaveValue(9.75);
  expect(screen.getByLabelText("1 美元兑换人民币")).toHaveValue(7.1);
  expect(screen.getByRole("button", {name: "保存计费设置"})).toBeEnabled();
  expect(read).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await screen.findByRole("status");
  expect(save).toHaveBeenCalledTimes(2);
  expect(save.mock.calls[1][0]).toEqual(save.mock.calls[0][0]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("an initial pricing read failure can retry without losing a recovered draft", async () => {
  const read = vi.spyOn(api, "studioPricing").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(pricing([]));
  const save = vi.spyOn(api, "updateStudioPricing").mockImplementation(async body => body);
  const user = userEvent.setup(); render(<PricingSettings />);
  await user.click(await screen.findByRole("button", {name: "重试计费设置"}));
  await screen.findByRole("button", {name: "保存计费设置"});
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/还没有单价/)).toBeVisible();
  await user.click(screen.getByRole("button", {name: "保存计费设置"}));
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({models: []})));
});

test("loads the DeepSeek reference preset without dropping other provider prices", async () => {
  const { save, user } = setup();
  const preset = vi.spyOn(api, "studioPricingPreset").mockResolvedValue({
    preset: "deepseek",
    models: [
      { provider: "deepseek", model: "deepseek-flash", input_usd_per_million: 0.30,
        cached_input_usd_per_million: 0.006, output_usd_per_million: 1.20, image_usd_per_image: null },
      { provider: "deepseek", model: "deepseek-v4-pro", input_usd_per_million: 1.32,
        cached_input_usd_per_million: 0.044, output_usd_per_million: 3.96, image_usd_per_image: null },
    ],
  });
  await user.click(await screen.findByRole("button", { name: "载入 2 个模型参考价" }));
  await screen.findByRole("status");
  expect(preset).toHaveBeenCalledWith("deepseek");
  expect(screen.getByLabelText("模型 2")).toHaveValue("deepseek-flash");
  expect(screen.getByLabelText("模型 3")).toHaveValue("deepseek-v4-pro");
  await user.click(screen.getByRole("button", { name: "保存计费设置" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const saved = save.mock.calls[0][0];
  expect(saved.models.map(row => `${row.provider}:${row.model}`)).toEqual([
    "codex:text-fixture", "deepseek:deepseek-flash", "deepseek:deepseek-v4-pro",
  ]);
});
