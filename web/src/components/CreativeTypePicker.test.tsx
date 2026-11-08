import { useState } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import CreativeTypePicker from "./CreativeTypePicker";
import {
  creativeTypesApi,
  creativeRef,
  type CreativeTypeRef,
} from "../api/creativeTypes";
import { typeLibrary, typeRecord } from "../test/creativeTypesFixture";
vi.mock("../api/creativeTypes", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/creativeTypes")>()),
  creativeTypesApi: { list: vi.fn() },
}));
it("requires confirmation before removing incompatible Tags and retains shared Tags", async () => {
  const library = typeLibrary([
    typeRecord(),
    typeRecord("genre.mystery", "悬疑"),
    typeRecord("tag.betrayal", "背叛"),
    typeRecord("tag.shared", "共享"),
  ]);
  library.items[3].genre_ids = ["genre.romance", "genre.mystery"];
  vi.mocked(creativeTypesApi.list).mockResolvedValue(library);
  function Form() {
    const [refs, setRefs] = useState<CreativeTypeRef[]>(
      [0, 2, 3].map((i) => creativeRef(library.items[i])),
    );
    return (
      <>
        <CreativeTypePicker refs={refs} onChange={(_, next) => setRefs(next)} />
        <output>{refs.map((r) => r.id).join(",")}</output>
      </>
    );
  }
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <Form />
    </MemoryRouter>,
  );
  await user.click(await screen.findByRole("radio", { name: "悬疑" }));
  expect(
    screen.getByRole("group", { name: "确认更换主类型" }),
  ).toHaveTextContent("背叛");
  expect(screen.getByRole("status")).toHaveTextContent(
    "genre.romance,tag.betrayal,tag.shared",
  );
  await user.click(screen.getByRole("button", { name: "保留当前选择" }));
  expect(
    screen.queryByRole("group", { name: "确认更换主类型" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: "悬疑" }));
  await user.click(screen.getByRole("button", { name: "确认更换" }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "genre.mystery,tag.shared",
  );
  expect(screen.getByRole("button", { name: "共享" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});
