"""Static analysis validator for Solidity strategies."""

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional


@dataclass
class ValidationResult:
    """Result of Solidity validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SolidityValidator:
    """Static analysis validator for user-submitted Solidity strategies.

    Ensures strategies:
    - Inherit from AMMStrategyBase
    - Define required functions (afterInitialize, afterSwap, getName)
    - Don't use dangerous patterns (external calls, assembly, selfdestruct, etc.)
    """

    # Dangerous patterns that are blocked
    BLOCKED_PATTERNS = [
        # External calls
        (r"\.\s*call\s*(?:\{|\()", "External calls are not allowed"),
        (r"\.\s*delegatecall\s*\(", "delegatecall is not allowed"),
        (r"\.\s*staticcall\s*\(", "staticcall is not allowed"),
        (r"\.\s*callcode\s*\(", "callcode is not allowed"),
        # Dangerous operations
        (r"\bselfdestruct\s*\(", "selfdestruct is not allowed"),
        (r"\bsuicide\s*\(", "suicide is not allowed"),
        # Assembly (could bypass restrictions)
        (r"\bassembly\b(?:\s*\([^)]*\))?\s*\{", "Inline assembly is not allowed"),
        # Creating other contracts
        (r"\bnew\s+\w+\s*\(", "Creating new contracts is not allowed"),
        # External code introspection
        (r"\.\s*code(?:hash)?\b", "Reading code from external addresses is not allowed"),
        # Low-level address calls
        (r"\.transfer\s*\(", "transfer() is not allowed"),
        (r"\.send\s*\(", "send() is not allowed"),
        # Block manipulation hints
        (r"\bcoinbase\b", "block.coinbase access is not allowed"),
        # External contract interactions
        (r"interface\s+\w+\s*\{(?![\s\S]*IAMMStrategy)", "Custom interfaces are not allowed"),
    ]

    # Required patterns
    REQUIRED_PATTERNS = [
        # Must implement afterInitialize
        (
            r"function\s+afterInitialize\s*\(",
            "Must implement afterInitialize(uint256, uint256) function",
        ),
        # Must implement afterSwap
        (
            r"function\s+afterSwap\s*\(",
            "Must implement afterSwap(TradeInfo calldata) function",
        ),
        # Must implement getName
        (
            r"function\s+getName\s*\(",
            "Must implement getName() function",
        ),
    ]

    # Allowed imports (only base contracts)
    ALLOWED_IMPORT_PATHS = {
        "AMMStrategyBase.sol",
        "IAMMStrategy.sol",
    }

    RESERVED_IDENTIFIERS = {
        "AMMStrategyBase",
        "IAMMStrategy",
        "TradeInfo",
    }

    def validate(self, source_code: str) -> ValidationResult:
        """Validate Solidity source code.

        Args:
            source_code: The Solidity source code to validate

        Returns:
            ValidationResult with valid flag and any errors/warnings
        """
        errors: list[str] = []
        warnings: list[str] = []
        analysis_source = self._preprocess_source(source_code, strip_strings=True)
        import_source = self._preprocess_source(source_code, strip_strings=False)

        # Check for required pragma
        if not re.search(r"pragma\s+solidity\s+", analysis_source):
            errors.append("Missing pragma solidity directive")

        # Check SPDX license identifier (warning only)
        if not re.search(r"//\s*SPDX-License-Identifier:", source_code):
            warnings.append("Missing SPDX license identifier")

        # Check for blocked patterns
        for pattern, message in self.BLOCKED_PATTERNS:
            if re.search(pattern, analysis_source, re.IGNORECASE):
                errors.append(message)

        contract_errors = self._validate_contract_declaration(analysis_source)
        errors.extend(contract_errors)

        # Check for required patterns
        for pattern, message in self.REQUIRED_PATTERNS:
            if not re.search(pattern, analysis_source):
                errors.append(message)

        # Validate imports
        import_errors = self._validate_imports(import_source)
        errors.extend(import_errors)

        # Prevent shadowing core interface/base names
        redeclaration_errors = self._check_reserved_redeclarations(import_source)
        errors.extend(redeclaration_errors)

        # Check for storage outside of slots array
        storage_warnings = self._check_storage_usage(import_source)
        warnings.extend(storage_warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _preprocess_source(self, source_code: str, *, strip_strings: bool) -> str:
        """Strip comments and string literals before structural regex checks."""
        # Remove multiline comments first
        source = re.sub(r"/\*[\s\S]*?\*/", "", source_code)
        # Remove single-line comments
        source = re.sub(r"//.*?$", "", source, flags=re.MULTILINE)
        if strip_strings:
            # Remove string/char literals so blocked/required patterns
            # cannot be satisfied by quoted text.
            source = re.sub(r'"(?:\\.|[^"\\])*"', '""', source)
            source = re.sub(r"'(?:\\.|[^'\\])*'", "''", source)
        return source

    def _validate_contract_declaration(self, source_code: str) -> list[str]:
        """Require `contract Strategy is ...` with AMMStrategyBase in inheritance list."""
        errors = []
        contract_match = re.search(r"\bcontract\s+Strategy\s+is\s+([^{}]+)\{", source_code)
        if not contract_match:
            errors.append(
                "Contract must be named 'Strategy' and inherit from AMMStrategyBase"
            )
            return errors

        base_list = contract_match.group(1)
        base_names = []
        for base in base_list.split(","):
            cleaned = base.strip()
            if not cleaned:
                continue
            # Keep only the base contract/interface identifier
            name_match = re.match(r"([A-Za-z_]\w*)", cleaned)
            if name_match:
                base_names.append(name_match.group(1))

        if "AMMStrategyBase" not in base_names:
            errors.append(
                "Contract must be named 'Strategy' and inherit from AMMStrategyBase"
            )

        return errors

    def _validate_imports(self, source_code: str) -> list[str]:
        """Validate that only allowed imports are used.

        Args:
            source_code: The source code to check

        Returns:
            List of error messages for invalid imports
        """
        errors = []

        # Find all import statements
        import_pattern = r'import\s+(?:[\{][\w\s,]+[\}]\s+from\s+)?["\']([^"\']+)["\']'
        imports = re.findall(import_pattern, source_code)

        if not imports:
            errors.append(
                "Missing required imports. "
                "Only './AMMStrategyBase.sol' and './IAMMStrategy.sol' are allowed."
            )
            return errors

        seen = set()
        for import_path in imports:
            normalized = self._normalize_import_path(import_path)
            if normalized is None or normalized not in self.ALLOWED_IMPORT_PATHS:
                errors.append(
                    f"Import '{import_path}' is not allowed. "
                    "Only './AMMStrategyBase.sol' and './IAMMStrategy.sol' are allowed."
                )
                continue
            seen.add(normalized)

        missing = self.ALLOWED_IMPORT_PATHS - seen
        if missing:
            errors.append(
                "Missing required base imports: "
                + ", ".join(sorted(f"'./{path}'" for path in missing))
            )

        return errors

    def _normalize_import_path(self, import_path: str) -> Optional[str]:
        """Normalize and validate a Solidity import path.

        Returns:
            Canonical path string if safe, otherwise None.
        """
        if not import_path or "\\" in import_path:
            return None

        if import_path.startswith("/"):
            return None

        raw = PurePosixPath(import_path)
        parts = list(raw.parts)
        if not parts:
            return None

        filename = parts[-1]
        if not filename:
            return None

        # Allow only relative prefixes made of "." / ".." before filename.
        # This supports templates located in nested folders (e.g. ../AMMStrategyBase.sol)
        # while still restricting imports to the two allowed base files.
        for part in parts[:-1]:
            if part not in ("", ".", ".."):
                return None

        return filename

    def _check_reserved_redeclarations(self, source_code: str) -> list[str]:
        """Reject user source that redefines reserved base/interface names."""
        errors = []
        pattern = r"\b(contract|interface|library|struct|enum)\s+([A-Za-z_]\w*)\b"
        for _, name in re.findall(pattern, source_code):
            if name in self.RESERVED_IDENTIFIERS:
                errors.append(
                    f"Redefining reserved identifier '{name}' is not allowed."
                )
        return errors

    def _check_storage_usage(self, source_code: str) -> list[str]:
        """Check for potential storage variables outside the slots array.

        This is a heuristic check - the actual enforcement is at the EVM level.

        Args:
            source_code: The source code to check

        Returns:
            List of warning messages
        """
        warnings = []

        # Look for state variable declarations (outside function bodies)
        # This is a simple heuristic - not perfect but catches common cases

        # Pattern for state variable declarations
        # Matches things like: uint256 myVar; or mapping(...) myMap;
        state_var_pattern = r"^\s*(uint\d*|int\d*|bool|address|bytes\d*|string|mapping\s*\([^)]+\))\s+(?!constant|immutable)(\w+)\s*[;=]"

        # Find the contract body
        contract_match = re.search(r"contract\s+Strategy\s+is\s+[^{}]+\{", source_code)
        if contract_match:
            # Get content after contract declaration
            contract_body = source_code[contract_match.end() :]

            # Remove function bodies to only check contract-level declarations
            # This is a simplification - proper parsing would require a Solidity parser
            depth = 1
            contract_level_code = ""
            i = 0
            while i < len(contract_body) and depth > 0:
                char = contract_body[i]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                elif depth == 1:
                    contract_level_code += char
                i += 1

            # Check for state variables
            for line in contract_level_code.split("\n"):
                match = re.match(state_var_pattern, line)
                if match:
                    var_name = match.group(2)
                    # Ignore known safe patterns
                    if var_name not in ["slots", "WAD", "MAX_FEE", "MIN_FEE", "BPS"]:
                        warnings.append(
                            f"State variable '{var_name}' declared outside slots array. "
                            "Use slots[0-31] for persistent storage to ensure storage limits."
                        )

        return warnings

    def quick_check(self, source_code: str) -> tuple[bool, Optional[str]]:
        """Quick validation check for basic requirements.

        Args:
            source_code: The source code to check

        Returns:
            Tuple of (is_valid, error_message)
        """
        result = self.validate(source_code)
        if result.valid:
            return True, None
        return False, result.errors[0] if result.errors else "Unknown validation error"
