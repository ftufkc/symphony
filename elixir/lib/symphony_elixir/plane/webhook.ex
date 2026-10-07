defmodule SymphonyElixir.Plane.Webhook do
  @moduledoc "Raw-body HMAC verification and Plane bot-mention ingestion."
  @behaviour Plug
  import Plug.Conn
  alias SymphonyElixir.Plane.Runtime
  @impl true
  def init(opts), do: opts
  @impl true
  def call(%{method: "POST", request_path: path} = conn, opts) when path in ["/webhook", "/webhook/"] do
    with {:ok, body, conn} <- read_body(conn, length: 1_048_576),
         [signature] <- get_req_header(conn, "x-plane-signature"),
         true <- verify_signature(Keyword.fetch!(opts, :secret), body, signature),
         {:ok, payload} when is_map(payload) <- Jason.decode(body),
         :ok <- Runtime.accept(payload) do
      send_resp(conn, 200, "ok")
    else
      {:more, _body, conn} -> send_resp(conn, 413, "payload too large")
      false -> send_resp(conn, 401, "invalid signature")
      [] -> send_resp(conn, 401, "missing signature")
      {:error, %Jason.DecodeError{}} -> send_resp(conn, 400, "invalid JSON")
      {:ok, _payload} -> send_resp(conn, 400, "invalid payload")
      _ -> send_resp(conn, 503, "not accepted")
    end
  end

  def call(conn, _opts), do: send_resp(conn, 404, "not found")
  @spec verify_signature(String.t(), binary(), term()) :: boolean()
  def verify_signature(secret, body, signature) when is_binary(secret) and byte_size(secret) > 0 and is_binary(signature) do
    expected = :crypto.mac(:hmac, :sha256, secret, body) |> Base.encode16(case: :lower)
    byte_size(signature) == byte_size(expected) and Plug.Crypto.secure_compare(expected, signature)
  end

  def verify_signature(_secret, _body, _signature), do: false
  @spec parse_event(map(), String.t()) :: map() | nil
  def parse_event(payload, bot) do
    data = payload["data"] || %{}
    pid = entity_id(data["project"])
    wid = entity_id(data["work_item"] || data["issue"] || data["issue_id"])

    if actionable?(payload) and valid_identity?(pid, wid, data["id"]) and mentioned_by_human?(payload, bot) do
      %{id: pid <> "/" <> wid, comment_id: data["id"]}
    end
  end

  defp actionable?(payload) do
    payload["event"] in ["issue_comment", "work_item_comment"] and payload["action"] in ["create", "created"]
  end

  defp valid_identity?(pid, wid, comment), do: is_binary(pid) and is_binary(wid) and is_binary(comment)

  defp mentioned_by_human?(payload, bot) do
    data = payload["data"] || %{}
    actors = [(payload["activity"] || %{})["actor"], data["created_by"], data["actor"]] |> Enum.map(&entity_id/1)

    mentions =
      Regex.scan(
        ~r/<mention-component\b[^>]*\bentity_identifier=["']([^"']+)["'][^>]*>/i,
        data["comment_html"] || ""
      )
      |> Enum.map(&List.last/1)

    bot not in actors and bot in mentions
  end

  defp entity_id(%{"id" => id}), do: id
  defp entity_id(id) when is_binary(id), do: id
  defp entity_id(_value), do: nil
end
