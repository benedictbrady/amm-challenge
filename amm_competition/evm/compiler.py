"""Solidity compilation service using py-solc-x."""

import solcx
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CompilationResult:
    """Result of Solidity compilation."""

    success: bool
    bytecode: Optional[bytes] = None
    deployed_bytecode: Optional[bytes] = None
    abi: Optional[list] = None
    errors: Optional[list[str]] = None
    warnings: Optional[list[str]] = None


class SolidityCompiler:
    """Compiles Solidity strategies using py-solc-x.

    Uses inline sources to avoid filesystem dependencies.
    """

    SOLC_VERSION = "0.8.24"

    # Path to the contracts directory with base contracts
    CONTRACTS_DIR = Path(__file__).parent.parent.parent / "contracts"
    CONTRACTS_SRC_DIR = CONTRACTS_DIR / "src"

    # Opcodes we never allow in user runtime bytecode.
    FORBIDDEN_OPCODES = {
        0x31: "BALANCE",
        0x3B: "EXTCODESIZE",
        0x3C: "EXTCODECOPY",
        0x3F: "EXTCODEHASH",
        0xF0: "CREATE",
        0xF1: "CALL",
        0xF2: "CALLCODE",
        0xF4: "DELEGATECALL",
        0xF5: "CREATE2",
        0xFA: "STATICCALL",
        0xFF: "SELFDESTRUCT",
    }

    def __init__(self):
        """Initialize the compiler and ensure solc is installed."""
        self._ensure_solc_installed()

    def _ensure_solc_installed(self) -> None:
        """Install solc if not already installed."""
        installed = [str(v) for v in solcx.get_installed_solc_versions()]
        if self.SOLC_VERSION not in installed:
            solcx.install_solc(self.SOLC_VERSION)

    def _load_base_contracts(self) -> dict[str, str]:
        """Load base contract sources from the contracts directory."""
        sources = {}
        base_contracts = ["IAMMStrategy.sol", "AMMStrategyBase.sol"]
        for contract in base_contracts:
            src_file = self.CONTRACTS_DIR / "src" / contract
            if src_file.exists():
                sources[contract] = src_file.read_text()
        return sources

    def compile(self, source_code: str, contract_name: str = "Strategy") -> CompilationResult:
        """Compile Solidity source code.

        Args:
            source_code: The Solidity source code (must define a contract named `contract_name`)
            contract_name: Name of the contract to extract (default: "Strategy")

        Returns:
            CompilationResult with bytecode, ABI, and any errors
        """
        errors: list[str] = []
        warnings: list[str] = []

        try:
            # Load base contracts
            base_sources = self._load_base_contracts()

            # Build sources dict with all contracts
            sources = {
                "Strategy.sol": {"content": source_code},
            }
            for name, content in base_sources.items():
                sources[name] = {"content": content}

            # Build compile_standard input
            input_json = {
                "language": "Solidity",
                "sources": sources,
                "settings": {
                    "optimizer": {
                        "enabled": True,
                        "runs": 200,
                    },
                    "viaIR": True,
                    "evmVersion": "paris",
                    "outputSelection": {
                        "*": {
                            "*": [
                                "abi",
                                "evm.bytecode.object",
                                "evm.deployedBytecode.object",
                                "storageLayout",
                            ],
                        },
                    },
                },
            }

            # Compile
            output = solcx.compile_standard(
                input_json,
                solc_version=self.SOLC_VERSION,
                base_path=str(self.CONTRACTS_SRC_DIR),
                allow_paths=str(self.CONTRACTS_SRC_DIR),
            )

            # Check for errors in output
            if "errors" in output:
                for err in output["errors"]:
                    severity = err.get("severity", "error")
                    message = err.get("formattedMessage", err.get("message", "Unknown error"))
                    if severity == "error":
                        errors.append(message)
                    elif severity == "warning":
                        warnings.append(message)

            if errors:
                return CompilationResult(
                    success=False,
                    errors=errors,
                    warnings=warnings,
                )

            # Extract bytecode and ABI from the output
            contracts = output.get("contracts", {})
            strategy_contracts = contracts.get("Strategy.sol", {})

            if contract_name not in strategy_contracts:
                available = list(strategy_contracts.keys())
                return CompilationResult(
                    success=False,
                    errors=[
                        f"Contract '{contract_name}' not found in output. "
                        f"Available contracts: {available}"
                    ],
                    warnings=warnings,
                )

            contract_output = strategy_contracts[contract_name]
            abi = contract_output.get("abi", [])
            evm = contract_output.get("evm", {})

            bytecode_hex = evm.get("bytecode", {}).get("object", "")
            deployed_bytecode_hex = evm.get("deployedBytecode", {}).get("object", "")

            if not bytecode_hex:
                return CompilationResult(
                    success=False,
                    errors=["No bytecode in compiled output"],
                    warnings=warnings,
                )

            creation_bytecode = bytes.fromhex(bytecode_hex)
            deployed_bytecode = (
                bytes.fromhex(deployed_bytecode_hex) if deployed_bytecode_hex else b""
            )

            # Enforce forbidden-opcode policy in creation/init code too.
            creation_hits = self._scan_forbidden_opcodes(creation_bytecode)
            if creation_hits:
                return CompilationResult(
                    success=False,
                    errors=[
                        "Creation bytecode contains forbidden opcodes: "
                        + ", ".join(creation_hits)
                    ],
                    warnings=warnings,
                )

            # Enforce forbidden-opcode policy directly on deployed runtime code.
            forbidden_hits = self._scan_forbidden_opcodes(deployed_bytecode)
            if forbidden_hits:
                return CompilationResult(
                    success=False,
                    errors=[
                        "Runtime bytecode contains forbidden opcodes: "
                        + ", ".join(forbidden_hits)
                    ],
                    warnings=warnings,
                )

            # Enforce storage policy from compiler-provided layout.
            storage_layout = contract_output.get("storageLayout", {})
            storage_entries = storage_layout.get("storage", [])
            storage_errors = self._validate_storage_layout(storage_entries)
            if storage_errors:
                return CompilationResult(
                    success=False,
                    errors=storage_errors,
                    warnings=warnings,
                )

            return CompilationResult(
                success=True,
                bytecode=creation_bytecode,
                deployed_bytecode=deployed_bytecode or None,
                abi=abi,
                warnings=warnings,
            )

        except solcx.exceptions.SolcError as e:
            return CompilationResult(
                success=False,
                errors=[f"Solidity compilation error: {str(e)}"],
            )
        except Exception as e:
            return CompilationResult(
                success=False,
                errors=[f"Compilation error: {str(e)}"],
            )

    def _scan_forbidden_opcodes(self, bytecode: bytes) -> list[str]:
        """Disassemble bytecode and report forbidden opcodes."""
        if not bytecode:
            return []

        # Solidity appends CBOR metadata to runtime bytecode.
        # The final 2 bytes encode metadata length; exclude that region
        # so static scanning only checks executable runtime instructions.
        code_len = len(bytecode)
        if code_len >= 2:
            metadata_len = int.from_bytes(bytecode[-2:], byteorder="big")
            if metadata_len + 2 <= code_len:
                code_len = code_len - metadata_len - 2

        hits: list[str] = []
        i = 0
        while i < code_len:
            op = bytecode[i]
            name = self.FORBIDDEN_OPCODES.get(op)
            if name is not None:
                hits.append(f"{name}@0x{i:x}")

            # PUSH1..PUSH32 contain inline data, skip immediate bytes.
            if 0x60 <= op <= 0x7F:
                i += 1 + (op - 0x5F)
            else:
                i += 1

        return hits

    def _validate_storage_layout(self, storage_entries: list[dict]) -> list[str]:
        """Validate strategy storage layout is limited to AMMStrategyBase.slots."""
        errors: list[str] = []
        for entry in storage_entries:
            label = entry.get("label")
            slot = entry.get("slot")
            offset = entry.get("offset")

            # The only permitted storage entry is the inherited `slots` array at slot 0.
            if label == "slots" and str(slot) == "0" and str(offset) == "0":
                continue

            errors.append(
                "State storage outside AMMStrategyBase.slots[0..31] is not allowed "
                f"(found '{label}' at slot {slot}, offset {offset})."
            )

        return errors

    def compile_and_get_bytecode(self, source_code: str) -> tuple[bytes, list]:
        """Convenience method to compile and return bytecode directly.

        Args:
            source_code: Solidity source code

        Returns:
            Tuple of (bytecode, abi)

        Raises:
            RuntimeError: If compilation fails
        """
        result = self.compile(source_code)
        if not result.success:
            raise RuntimeError(f"Compilation failed: {'; '.join(result.errors or [])}")
        return result.bytecode, result.abi
