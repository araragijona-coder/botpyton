"""Evidence-based failure registry. Signatures are leads, never verified root causes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailureRule:
    rule_id: str
    signatures: tuple[str, ...]
    category: str
    strategy: str
    diagnostic_steps: tuple[str, ...]
    safe_experiment: str
    stop_condition: str
    reference_url: str
    automatic_retry: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    category: str
    strategy: str | None
    retryable: bool
    reason: str
    rule_id: str | None = None
    diagnostic_steps: tuple[str, ...] = ()
    safe_experiment: str = ""
    stop_condition: str = ""
    reference_url: str | None = None


# Curated diagnostic patterns span common Python, JavaScript/TypeScript, Go, Rust,
# Java and C# messages. Matching supplies investigation steps only; none authorizes
# a source edit, dependency install, privileged operation, or automatic retry.
KNOWN_RULES = (
    FailureRule(
        "PY-IMPORT-MISSING", ("ModuleNotFoundError", "No module named"),
        "test_environment", "verify_declared_dependencies",
        ("Check which Python interpreter runs the command.",
         "Compare the import name with declared dependencies and lockfiles.",
         "Check whether the dependency is installed in that exact environment."),
        "Inspect project metadata and interpreter paths; do not install anything automatically.",
        "Stop if the module is not declared or the active environment cannot be identified.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-IMPORT-CONTRACT", ("ImportError",),
        "import_contract", "inspect_import_contract",
        ("Distinguish a missing module from a missing symbol within an existing module.",
         "Inspect the import statement, module version, and exported names.",
         "Check for circular imports before changing package structure."),
        "Compare the requested symbol with the local module/API without changing dependencies.",
        "Stop before upgrading or adding a package if the supported API version is unknown.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-INDENTATION", ("IndentationError",),
        "source_syntax", "inspect_syntax_location",
        ("Read the exact reported line and surrounding block.",
         "Check consistent indentation and block boundaries.",
         "Use syntax validation before running the program."),
        "Make a minimal whitespace/block correction only when the parser location is unambiguous.",
        "Stop if fixing indentation would require guessing the intended control flow.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-SYNTAX", ("SyntaxError",),
        "source_syntax", "inspect_syntax_location",
        ("Use the reported file, line, and column.",
         "Inspect nearby delimiters, operators, strings, and control-flow headers.",
         "Validate the smallest correction with the language parser."),
        "Reproduce the parser error and test one minimal syntax change.",
        "Stop if the parser location does not uniquely identify a safe correction.",
        "https://docs.python.org/3/library/ast.html",
    ),
    FailureRule(
        "DATA-JSON", ("JSONDecodeError",),
        "invalid_json", "validate_json_schema",
        ("Capture the filename, line, and column.",
         "Validate syntax separately from application schema.",
         "Check encoding and the producer of the invalid data."),
        "Parse the original file read-only and identify the exact invalid token before proposing a fix.",
        "Stop before overwriting persisted data; preserve a recoverable copy first.",
        "https://docs.python.org/3/library/json.html",
    ),
    FailureRule(
        "GIT-MERGE-CONFLICT", ("CONFLICT (content)",),
        "git_conflict", "rebuild_isolated_branch",
        ("Identify every file with conflict markers.",
         "Compare base, local, and incoming versions for the affected hunk.",
         "Run targeted tests after resolving each conflict."),
        "Resolve on an isolated branch while preserving both intended behaviors.",
        "Stop if ownership or intended behavior cannot be inferred from source and tests.",
        "https://git-scm.com/docs/git-merge",
    ),
    FailureRule(
        "PY-CALL-SIGNATURE", ("TypeError",),
        "call_contract", "inspect_call_contract",
        ("Inspect the exact callable signature and the provided arguments.",
         "Check runtime types, optional parameters, and version differences.",
         "Reduce the failure to a minimal input."),
        "Compare the call with the declared signature and add a regression case before changing it.",
        "Stop if the intended input contract is unclear or the proposed coercion could lose data.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-OBJECT-ATTRIBUTE", ("AttributeError",),
        "object_contract", "inspect_object_contract",
        ("Record the runtime type and initialization path of the receiver.",
         "Check spelling, expected protocol, and library version.",
         "Distinguish an absent attribute from an uninitialized object."),
        "Trace the object's construction and compare it with the expected interface.",
        "Stop before adding a compatibility shim if the supported type/version is unknown.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-NAME-SCOPE", ("NameError",),
        "name_resolution", "inspect_identifier_scope",
        ("Check identifier spelling and definition site.",
         "Inspect imports, function/class scope, and execution order.",
         "Confirm that the identifier exists on the failing path."),
        "Trace the identifier definition to the failing use; do not introduce a guessed global.",
        "Stop if multiple identifiers or scopes could explain the failure.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-MAPPING-KEY", ("KeyError",),
        "data_contract", "inspect_mapping_schema",
        ("Capture the mapping shape and actual keys at the failure point.",
         "Compare producer and consumer schema versions.",
         "Decide whether a missing field is invalid input or an optional value."),
        "Use a minimal fixture to test both present and missing-key behavior.",
        "Stop before defaulting a missing field if doing so could hide corrupted or required data.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PY-SEQUENCE-BOUNDS", ("IndexError",),
        "boundary_condition", "inspect_sequence_bounds",
        ("Record the sequence length and index for the failing input.",
         "Check empty, singleton, and boundary-length cases.",
         "Review any off-by-one arithmetic and iteration assumptions."),
        "Add a regression case for the smallest input that reproduces the boundary failure.",
        "Stop if the intended empty-input behavior is not specified.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PATH-MISSING", ("FileNotFoundError", "ENOENT", "No such file or directory"),
        "path_resolution", "inspect_file_path",
        ("Record the current working directory and the exact requested path.",
         "Check whether the file is generated, optional, renamed, or outside the project root.",
         "Check for case-sensitivity and path-separator differences."),
        "Re-run a read-only path-existence check against the normalized intended path.",
        "Stop rather than creating a replacement file when its required contents or ownership are unknown.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "PATH-PERMISSION", ("PermissionError", "EACCES", "Access is denied"),
        "permission_or_lock", "inspect_path_permissions",
        ("Check the exact path, owner, access rights, and whether a process holds the file open.",
         "Determine whether the failure is access control or a transient sharing lock.",
         "Keep the operation at the current privilege level while diagnosing."),
        "Inspect permissions and file-lock state without elevating privileges.",
        "Stop before requesting administrator rights, disabling controls, or changing ACLs.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "OP-TIMEOUT", ("TimeoutExpired", "TimeoutError", "ETIMEDOUT", "timed out"),
        "timeout", "isolate_slow_step",
        ("Identify the exact operation and configured timeout.",
         "Record input size, output volume, and the last completed stage.",
         "Measure the slowest isolated step before changing limits."),
        "Reproduce with a bounded input and one stage; do not only increase the timeout.",
        "Stop if the child process cannot be bounded or its side effects are unknown.",
        "https://docs.python.org/3/library/subprocess.html",
    ),
    FailureRule(
        "TEST-ASSERTION", ("AssertionError", "FAILED (failures="),
        "test_contract", "compare_expected_actual",
        ("Record expected and actual values and the smallest failing input.",
         "Check whether implementation or test assumptions changed.",
         "Review recent related edits and adjacent tests."),
        "Write a minimal regression test that expresses the intended public behavior.",
        "Stop if expected behavior is disputed or inferred only from the failing test.",
        "https://pytest.org/en/stable/changelog.html",
    ),
    FailureRule(
        "RESOURCE-RECURSION", ("RecursionError", "maximum recursion depth exceeded"),
        "resource_or_algorithm", "inspect_recursion_depth",
        ("Capture the recursive call path and input size.",
         "Look for cyclic structures, missing base cases, or unexpectedly deep input.",
         "Check whether an iterative algorithm can preserve the intended behavior."),
        "Use a small fixture to locate the repeated state; do not raise the recursion limit blindly.",
        "Stop if the input may be adversarial or if the termination condition is unknown.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "RESOURCE-MEMORY", ("MemoryError", "out of memory", "heap out of memory"),
        "resource_exhaustion", "measure_memory_pressure",
        ("Record available memory, input size, and the operation's peak working set.",
         "Check unbounded reads, retained collections, duplicate buffers, and parallel workers.",
         "Use a representative bounded workload for measurements."),
        "Measure a smaller input and identify the allocation source before changing the algorithm.",
        "Stop before increasing memory limits, deleting user data, or launching parallel jobs.",
        "https://docs.python.org/3/library/exceptions.html",
    ),
    FailureRule(
        "NODE-MODULE", ("ERR_MODULE_NOT_FOUND", "Cannot find module"),
        "build_environment", "resolve_javascript_module",
        ("Identify the active Node version and package manager.",
         "Check import path, package.json exports, working directory, and lockfile.",
         "Distinguish a local file path typo from a missing declared package."),
        "Inspect package metadata and the referenced path; do not run an automatic install.",
        "Stop if the required package source/version cannot be verified.",
        "https://nodejs.org/api/errors.html",
    ),
    FailureRule(
        "TYPESCRIPT-MODULE", ("TS2307",),
        "build_environment", "resolve_typescript_module",
        ("Check module specifier, resolution settings, declaration files, and package exports.",
         "Confirm the TypeScript and package versions.",
         "Check whether the dependency is declared and present in the active workspace."),
        "Inspect tsconfig and the manifest without changing package versions.",
        "Stop before changing module resolution globally or installing packages.",
        "https://www.typescriptlang.org/docs/",
    ),
    FailureRule(
        "TYPESCRIPT-TYPE", ("TS2322", "TS2345"),
        "type_contract", "inspect_typescript_contract",
        ("Compare the expected and actual types in the diagnostic.",
         "Trace generic/type inference and optional/null cases.",
         "Check whether the caller or callee contract is intended to change."),
        "Create the smallest type-checking example and prefer a compatible type correction over a cast.",
        "Stop if a proposed cast would suppress a meaningful safety check.",
        "https://www.typescriptlang.org/docs/handbook/2/everyday-types.html",
    ),
    FailureRule(
        "GO-MODULE", ("no required module provides package", "cannot find module providing package"),
        "build_environment", "resolve_go_module",
        ("Check the go.mod module path and import path spelling.",
         "Identify the module/workspace root used by the failing command.",
         "Check whether the dependency is declared and whether the requested version is available."),
        "Read go.mod/go.sum and reproduce with the repository's declared build profile.",
        "Stop before changing dependency versions or downloading modules without authorization.",
        "https://go.dev/wiki/Modules",
    ),
    FailureRule(
        "GO-SYMBOL", ("undefined:",),
        "name_resolution", "inspect_go_symbol",
        ("Check identifier spelling, package scope, build tags, and file inclusion.",
         "Confirm the failing package and target platform.",
         "Compare definitions with the source file selected by the build."),
        "Trace the symbol definition and use within the same package/build configuration.",
        "Stop if the build tags or target platform are not known.",
        "https://go.dev/doc/",
    ),
    FailureRule(
        "RUST-COMPILE", ("error[E",),
        "source_compile", "inspect_rust_compiler_diagnostic",
        ("Capture the complete compiler error code and source span.",
         "Read the compiler's explanation for that exact code and current toolchain.",
         "Inspect ownership, borrowing, trait bounds, and type information as applicable."),
        "Create a minimal reproduction and use the exact error code's official explanation.",
        "Stop if the apparent fix relies on unsafe code or weakens invariants without review.",
        "https://doc.rust-lang.org/error_codes/error-index.html",
    ),
    FailureRule(
        "JAVA-SYMBOL", ("cannot find symbol", "package does not exist"),
        "build_environment", "inspect_java_compile_resolution",
        ("Check symbol spelling, imports, package declarations, source roots, and classpath.",
         "Record the JDK and build-tool versions.",
         "Distinguish source visibility from a missing external dependency."),
        "Inspect the exact javac/build diagnostic and the declared build configuration.",
        "Stop before changing the classpath or upgrading dependencies without scope approval.",
        "https://docs.oracle.com/en/java/javase/21/docs/specs/man/javac.html",
    ),
    FailureRule(
        "CS-SYMBOL", ("CS0103",),
        "name_resolution", "inspect_csharp_scope",
        ("Check identifier spelling and the current block/class/namespace scope.",
         "Inspect using directives, assembly references, and target framework.",
         "Compare the error line with the current source, not a stale build artifact."),
        "Trace the identifier declaration and its visibility at the reported site.",
        "Stop if the target framework or intended scope is ambiguous.",
        "https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/compiler-messages/cs0103",
    ),
)


def classify_failure(
    log_text: str, *, attempt: int = 1, max_attempts: int = 3
) -> RecoveryDecision:
    """Classify a known signature but never authorize blind retries.

    retryable means the same task may automatically be retried without changing
    its inputs or hypothesis. Every current rule is false; a human or a verified
    repair must change the conditions before the task is re-enqueued.
    """
    if (
        isinstance(attempt, bool) or isinstance(max_attempts, bool)
        or not isinstance(attempt, int) or not isinstance(max_attempts, int)
        or attempt < 1 or max_attempts < 1 or attempt > max_attempts
    ):
        return RecoveryDecision(
            "attempt_limit", None, False,
            "Attempt count is outside configured bounds; stop and request intervention.",
            stop_condition="Do not claim or execute further attempts after the configured limit.",
        )
    lowered = log_text.casefold()
    for rule in KNOWN_RULES:
        matched = next((signature for signature in rule.signatures if signature.casefold() in lowered), None)
        if matched:
            return RecoveryDecision(
                category=rule.category,
                strategy=rule.strategy,
                retryable=rule.automatic_retry and attempt < max_attempts,
                reason=f"Diagnostic signature matched ({matched}); root cause is not yet confirmed.",
                rule_id=rule.rule_id,
                diagnostic_steps=rule.diagnostic_steps,
                safe_experiment=rule.safe_experiment,
                stop_condition=rule.stop_condition,
                reference_url=rule.reference_url,
            )
    return RecoveryDecision(
        "unknown", None, False,
        "No known failure signature matched; preserve evidence and ask for help rather than retrying blindly.",
        diagnostic_steps=(
            "Preserve the full error and the exact command and exit code.",
            "Gather bounded context from the nearest project frame or failing stage.",
            "Form one testable hypothesis and identify a reversible experiment.",
        ),
        safe_experiment="Ask for the missing log, reproduction steps, environment, or expected-versus-actual values.",
        stop_condition="Stop automatic recovery until evidence distinguishes plausible root causes.",
    )
