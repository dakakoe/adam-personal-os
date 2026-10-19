"""What to index, and where to read it from.

Contract addresses come from the protocol's own deployment configs
(github.com/kbizikav/zERC20, config/deployed/mainnet), except the zJPYC
LiquidityManager, which is not published there and was located on-chain.

Chain labels are the ones the dashboard already displays, so indexed rows drop
straight into the existing front end.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── event signatures ────────────────────────────────────────────────────────
# keccak("Transfer(address,address,uint256)")
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# keccak("Teleport(address,uint256)") — emitted by the zERC20 token when the
# Verifier mints a proven private transfer. The only way to tell a redeem from
# a wrap; both are mints from the zero address.
TELEPORT = "0xb7d7623245cb52e55db0c4265c68f0507e52aadd4f195cfdafaf1ea5d107e72d"
# keccak("Unwrapped(address,address,uint256,uint256)") — LiquidityManager,
# data = (amountOut, feeAmount).
UNWRAPPED = "0x0004d6f644fc2d087d5be8fde32a4db2f8c58d96f5bb217130b5ca6d5af8f21d"

ZERO = "0x0000000000000000000000000000000000000000"

# token -> (address, decimals). Addresses are deterministic (CREATE3), so a
# token keeps one address across every chain it exists on.
TOKENS: dict[str, tuple[str, int]] = {
    "zETH":  ("0x410056c6f0a9abd8c42b9eef3bb451966fb0d924", 18),
    "zUSDC": ("0xeb81ab55bc7aa89d1e0e3f60597d86e37702af53", 6),
    "zBNB":  ("0x4388d5618b9e13bd580209cdf37a202778c75c54", 18),
    "zJPYC": ("0xd33bed4ae7fe50107c532f63740b1bf2dabf2b30", 18),
}

# LiquidityManager -> token it custodies. Only chains where wrap/unwrap is
# available have one; elsewhere the zAsset is transferable but not redeemable.
LIQUIDITY_MANAGERS: dict[str, str] = {
    "0xcc10b7098fef1ab2f0ff3be91d2a7b3230b90cf0": "zETH",
    "0x04be137df79be7b5f3314c4a84d1c5e0d99bd477": "zUSDC",
    "0x39cc069df606c7bc8c79b0add0696bcaf548efd9": "zBNB",
    "0x12609c3a7a1a212953417c90472cdcf034965a1c": "zJPYC",
}


@dataclass(frozen=True)
class Chain:
    name: str                      # label shown in the dashboard
    tokens: tuple[str, ...]
    managers: tuple[str, ...]
    # Deployment block where known, else None and the start block is found by
    # binary search on block timestamps against Config.start_date.
    start_block: int | None
    # Blocks per eth_getLogs call, for a PUBLIC endpoint. Providers differ
    # wildly: Base's public node caps at 2k and BSC's public nodes refuse
    # eth_getLogs outright. The client halves this on a range error, so these
    # are starting points, not limits.
    chunk: int
    public_rpcs: tuple[str, ...]
    alchemy_net: str | None = None
    # Wider range allowed once a keyed provider (Alchemy / NodeReal) is in use.
    keyed_chunk: int = 10_000
    nodereal_net: str | None = None        # NodeReal only fronts Ethereum and BSC
    etherscan_chain_id: int | None = None  # Etherscan V2 chainid, free plan only
    # Provider preference, measured from the droplet on 2026-09-20. Public RPCs
    # are last because Cloudflare answers 403 to datacenter IPs on most of them.
    sources: tuple[str, ...] = ("public",)
    # Seconds between log windows. Kaia's public node starts returning 429
    # partway through a backfill at the default pace.
    pace: float = 0.12


_ETH_MANAGERS = ("0xcc10b7098fef1ab2f0ff3be91d2a7b3230b90cf0",
                 "0x04be137df79be7b5f3314c4a84d1c5e0d99bd477")
_ETH_TOKENS = ("zETH", "zUSDC", "zBNB")

CHAINS: dict[str, Chain] = {
    "Ethereum": Chain("Ethereum", _ETH_TOKENS, _ETH_MANAGERS, 24383713, 10_000,
                      ("https://ethereum-rpc.publicnode.com", "https://eth.drpc.org",
                       "https://cloudflare-eth.com"), "eth-mainnet",
                      nodereal_net="eth-mainnet", etherscan_chain_id=1,
                      sources=("nodereal", "etherscan", "public")),
    "Arbitrum": Chain("Arbitrum", _ETH_TOKENS, _ETH_MANAGERS, 428548563, 20_000,
                      ("https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"),
                      "arb-mainnet", etherscan_chain_id=42161,
                      sources=("etherscan", "public")),
    "Base":     Chain("Base", _ETH_TOKENS, _ETH_MANAGERS, 41711005, 2_000,
                      ("https://mainnet.base.org", "https://base-rpc.publicnode.com"),
                      "base-mainnet",
                      # Base is the gap: Etherscan's free plan excludes chain 8453,
                      # NodeReal does not front Base, and Alchemy's free tier caps
                      # eth_getLogs at 10 blocks — so it is deliberately NOT tried
                      # here; it would fail on every run. Put "alchemy" back in
                      # front of "public" the day that key is on a paid plan.
                      sources=("public",)),
    "BNB Chain": Chain("BNB Chain", ("zBNB",), ("0x39cc069df606c7bc8c79b0add0696bcaf548efd9",),
                       None, 2_000, ("https://bsc-dataseed.binance.org",
                                     "https://bsc-dataseed1.bnbchain.org",
                                     "https://bsc-rpc.publicnode.com"),
                       nodereal_net="bsc-mainnet", sources=("nodereal", "public")),
    # Polygon's public nodes cap ranges at 1,000 blocks AND prune history, so a
    # backfill there only works through Alchemy; the public entries are a tail-
    # following fallback, not a backfill path.
    "Polygon":  Chain("Polygon", ("zJPYC",), ("0x12609c3a7a1a212953417c90472cdcf034965a1c",),
                      None, 1_000, ("https://polygon-bor-rpc.publicnode.com",
                                    "https://polygon.drpc.org"), "polygon-mainnet",
                      etherscan_chain_id=137, sources=("etherscan", "public")),
    # Kaia has no keyed provider in play; its own archive endpoint serves
    # January-era ranges fine, so it needs none.
    "Kaia":     Chain("Kaia", ("zJPYC",), ("0x12609c3a7a1a212953417c90472cdcf034965a1c",),
                      None, 2_000, ("https://archive-en.node.kaia.io",
                                    "https://public-en.node.kaia.io",
                                    # Ankr caps ranges at 1k blocks, which the
                                    # adaptive narrowing discovers on its own; it
                                    # is the way back in when kaia.io throttles.
                                    "https://rpc.ankr.com/klaytn"),
                      sources=("public",), pace=0.5),
}


def addresses(chain: Chain) -> list[str]:
    """Every contract whose logs this chain needs, tokens first."""
    return [TOKENS[t][0] for t in chain.tokens] + list(chain.managers)


def token_of(address: str) -> tuple[str, int] | None:
    """Map a contract address back to (token, decimals) — token or manager."""
    address = address.lower()
    for name, (addr, dec) in TOKENS.items():
        if addr == address:
            return name, dec
    token = LIQUIDITY_MANAGERS.get(address)
    if token:
        return token, TOKENS[token][1]
    return None


def chunk_for(chain: Chain, url: str) -> int:
    """Window size to start from, given which endpoint we ended up on."""
    if "etherscan" in url:
        # Etherscan accepts very wide ranges; the real limit is 10k records per
        # window, which the source handles by halving when it hits the cap.
        return 500_000
    keyed = "alchemy" in url or "nodereal" in url
    return chain.keyed_chunk if keyed else chain.chunk


def rpc_urls(chain: Chain, *, alchemy_key: str | None, nodereal_key: str | None) -> list[str]:
    """JSON-RPC endpoints for this chain, in the chain's own preference order.

    Etherscan is not a JSON-RPC endpoint and is handled separately; everything
    else resolves to a URL here, with public nodes as the last resort.
    """
    urls: list[str] = []
    for kind in chain.sources:
        if kind == "nodereal" and chain.nodereal_net and nodereal_key:
            urls.append(f"https://{chain.nodereal_net}.nodereal.io/v1/{nodereal_key}")
        elif kind == "alchemy" and chain.alchemy_net and alchemy_key:
            urls.append(f"https://{chain.alchemy_net}.g.alchemy.com/v2/{alchemy_key}")
        elif kind == "public":
            urls.extend(chain.public_rpcs)
    return urls or list(chain.public_rpcs)
