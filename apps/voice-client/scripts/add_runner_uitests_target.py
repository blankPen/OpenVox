#!/usr/bin/env python3
"""Add a RunnerUITests target to Runner.xcodeproj via raw text injection.

The pbxproj file is OpenStep ASCII plist. We insert the minimum blocks
needed for the Patrol pod to find a RunnerUITests target and link
against the patrol framework.

Strategy: generate 24-hex IDs (avoiding any existing ones), then splice
the necessary `/* Begin ... */ ... /* End ... */` sections into the
file at the right anchors.
"""
from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

PROJECT_PATH = (
    Path(__file__).resolve().parent.parent / "ios/Runner.xcodeproj/project.pbxproj"
)
SOURCE_BASENAME = "RunnerUITests.m"


def gen_id(used: set[str], prefix: str = "") -> str:
    while True:
        cand = (prefix or "") + secrets.token_hex(12).upper()
        if cand not in used:
            used.add(cand)
            return cand


def collect_ids(text: str) -> set[str]:
    return set(re.findall(r"\b[0-9A-F]{24}\b", text))


def find_runner_target_id(text: str) -> str | None:
    """Return the PBXNativeTarget id of the Runner target.

    Stock Flutter Runner.xcodeproj has Runner at id
    97C146ED1CF9000F007C117D. We accept that known id, then fall back to
    scanning for the `Build configuration list for PBXNativeTarget "Runner"`
    reference if the user customised it.
    """
    # Most common case: stock Flutter id.
    if "97C146ED1CF9000F007C117D" in text:
        return "97C146ED1CF9000F007C117D"
    # Generic scan: any PBXNativeTarget whose buildConfigurationList
    # references a list whose comment says "Runner".
    for m in re.finditer(
        r"(\w{24}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;[\s\S]*?"
        r"buildConfigurationList = (\w{24}) /\* Build configuration list for "
        r'PBXNativeTarget "([^"]+)" \*/;',
        text,
    ):
        if m.group(3) == "Runner":
            return m.group(1)
    return None


def main() -> int:
    if not PROJECT_PATH.exists():
        print(f"project file not found: {PROJECT_PATH}", file=sys.stderr)
        return 1

    text = PROJECT_PATH.read_text()

    if "RunnerUITests" in text:
        print("RunnerUITests references already present — skipping")
        return 0

    used = collect_ids(text)
    runner_id = find_runner_target_id(text)
    if runner_id is None:
        # Fall back to known hardcoded id from stock Flutter Runner.xcodeproj.
        runner_id = "97C146ED1CF9000F007C117D"
        if runner_id not in used:
            print(f"Using hardcoded Runner target id {runner_id}", file=sys.stderr)

    # ─── Allocate IDs ────────────────────────────────────────────
    src_ref_id = gen_id(used)
    prod_ref_id = gen_id(used)
    src_build_file_id = gen_id(used)
    group_id = gen_id(used)
    sources_phase_id = gen_id(used)
    frameworks_phase_id = gen_id(used)
    resources_phase_id = gen_id(used)
    config_debug_id = gen_id(used)
    config_release_id = gen_id(used)
    config_profile_id = gen_id(used)
    config_list_id = gen_id(used)
    target_id = gen_id(used)
    proxy_id = gen_id(used)
    dep_id = gen_id(used)

    # ─── 1. PBXBuildFile (sources phase entry) ──────────────────
    build_file_block = (
        f"\t\t{src_build_file_id} /* {SOURCE_BASENAME} in Sources */ = "
        f"{{isa = PBXBuildFile; fileRef = {src_ref_id} /* {SOURCE_BASENAME} */; }};\n"
    )
    text = re.sub(
        r"(/\* Begin PBXBuildFile section \*/\n)",
        r"\1" + build_file_block,
        text,
        count=1,
    )

    # ─── 2. PBXContainerItemProxy (target dep proxy) ────────────
    # Insert after PBXBuildFile section ends.
    container_proxy_block = (
        f"\t\t{proxy_id} /* PBXContainerItemProxy */ = {{\n"
        f"\t\t\tisa = PBXContainerItemProxy;\n"
        f"\t\t\tcontainerPortal = {used} /* Project object */;\n"
        f"\t\t\tproxyType = 1;\n"
        f"\t\t\tremoteGlobalIDString = {runner_id};\n"
        f"\t\t\tremoteInfo = Runner;\n"
        f"\t\t}};\n"
    )
    # placeholder — we'll inject this once we know the rootObject id.
    # Find rootObject id by parsing `rootObject = XXXXXXXX /* Project object */`.
    root_match = re.search(r"rootObject = (\w{24})", text)
    if not root_match:
        print("rootObject not found", file=sys.stderr)
        return 1
    root_id = root_match.group(1)
    used.add(root_id)

    container_proxy_block = (
        f"\t\t{proxy_id} /* PBXContainerItemProxy */ = {{\n"
        f"\t\t\tisa = PBXContainerItemProxy;\n"
        f"\t\t\tcontainerPortal = {root_id} /* Project object */;\n"
        f"\t\t\tproxyType = 1;\n"
        f"\t\t\tremoteGlobalIDString = {runner_id};\n"
        f"\t\t\tremoteInfo = Runner;\n"
        f"\t\t}};\n"
    )

    text = re.sub(
        r"(/\* End PBXBuildFile section \*/\n)",
        container_proxy_block + r"\1",
        text,
        count=1,
    )

    # ─── 3. PBXFileReference entries ────────────────────────────
    file_refs = (
        f"\t\t{prod_ref_id} /* RunnerUITests.xctest */ = "
        f"{{isa = PBXFileReference; explicitFileType = wrapper.cfbundle; "
        f"includeInIndex = 0; path = RunnerUITests.xctest; "
        f"sourceTree = BUILT_PRODUCTS_DIR; }};\n"
        f"\t\t{src_ref_id} /* {SOURCE_BASENAME} */ = "
        f"{{isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; "
        f"path = {SOURCE_BASENAME}; sourceTree = \"<group>\"; }};\n"
    )
    text = re.sub(
        r"(/\* End PBXFileReference section \*/\n)",
        file_refs + r"\1",
        text,
        count=1,
    )

    # ─── 4. PBXFrameworksBuildPhase (empty) ──────────────────────
    frameworks_block = (
        f"\t\t{frameworks_phase_id} /* Frameworks */ = {{\n"
        f"\t\t\tisa = PBXFrameworksBuildPhase;\n"
        f"\t\t\tbuildActionMask = 2147483647;\n"
        f"\t\t\tfiles = (\n"
        f"\t\t\t);\n"
        f"\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXFrameworksBuildPhase section \*/\n)",
        frameworks_block + r"\1",
        text,
        count=1,
    )

    # ─── 5. PBXGroup (RunnerUITests folder) ─────────────────────
    # Find the main group and append the RunnerUITests group reference.
    main_group_match = re.search(
        r"(\w{24}) /\* Runner \*/ = \{\s*isa = PBXGroup;",
        text,
    )
    if not main_group_match:
        print("Main Runner group not found", file=sys.stderr)
        return 1
    main_group_id = main_group_match.group(1)

    # Add RunnerUITests group as child of main group.
    text = text.replace(
        f"{main_group_id} /* Runner */ = {{\n"
        f"\t\t\tisa = PBXGroup;\n"
        f"\t\t\tchildren = (\n",
        f"{main_group_id} /* Runner */ = {{\n"
        f"\t\t\tisa = PBXGroup;\n"
        f"\t\t\tchildren = (\n"
        f"\t\t\t\t{group_id} /* RunnerUITests */,\n",
        1,
    )

    group_block = (
        f"\t\t{group_id} /* RunnerUITests */ = {{\n"
        f"\t\t\tisa = PBXGroup;\n"
        f"\t\t\tchildren = (\n"
        f"\t\t\t\t{src_ref_id} /* {SOURCE_BASENAME} */,\n"
        f"\t\t\t);\n"
        f"\t\t\tpath = RunnerUITests;\n"
        f"\t\t\tsourceTree = \"<group>\";\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXGroup section \*/\n)",
        group_block + r"\1",
        text,
        count=1,
    )

    # ─── 6. PBXNativeTarget ─────────────────────────────────────
    native_target_block = (
        f"\t\t{target_id} /* RunnerUITests */ = {{\n"
        f"\t\t\tisa = PBXNativeTarget;\n"
        f"\t\t\tbuildConfigurationList = {config_list_id} /* "
        f"Build configuration list for PBXNativeTarget \"RunnerUITests\" */;\n"
        f"\t\t\tbuildPhases = (\n"
        f"\t\t\t\t{sources_phase_id} /* Sources */,\n"
        f"\t\t\t\t{frameworks_phase_id} /* Frameworks */,\n"
        f"\t\t\t\t{resources_phase_id} /* Resources */,\n"
        f"\t\t\t);\n"
        f"\t\t\tbuildRules = (\n"
        f"\t\t\t);\n"
        f"\t\t\tdependencies = (\n"
        f"\t\t\t\t{dep_id} /* PBXTargetDependency */,\n"
        f"\t\t\t);\n"
        f"\t\t\tname = RunnerUITests;\n"
        f"\t\t\tproductName = RunnerUITests;\n"
        f"\t\t\tproductReference = {prod_ref_id} /* RunnerUITests.xctest */;\n"
        f"\t\t\tproductType = \"com.apple.product-type.bundle.ui-testing\";\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXNativeTarget section \*/\n)",
        native_target_block + r"\1",
        text,
        count=1,
    )

    # ─── 7. PBXProject.targets: append our target ───────────────
    # Find the PBXProject object's targets list and append our id.
    text = re.sub(
        r"(/\* Begin PBXProject section \*/\n\t\t\w{24} /\* Project object \*/ = \{\n"
        r"(?:\t\t\t[^\n]*\n)*?"
        r"\t\t\ttargets = \(\n)((?:\t\t\t\t\w{24} /\* [^*]+ \*/,\n)+)",
        lambda m: m.group(1) + m.group(2) + f"\t\t\t\t{target_id} /* RunnerUITests */,\n",
        text,
        count=1,
    )

    # ─── 8. PBXResourcesBuildPhase (empty) ───────────────────────
    resources_block = (
        f"\t\t{resources_phase_id} /* Resources */ = {{\n"
        f"\t\t\tisa = PBXResourcesBuildPhase;\n"
        f"\t\t\tbuildActionMask = 2147483647;\n"
        f"\t\t\tfiles = (\n"
        f"\t\t\t);\n"
        f"\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXResourcesBuildPhase section \*/\n)",
        resources_block + r"\1",
        text,
        count=1,
    )

    # ─── 9. PBXSourcesBuildPhase ────────────────────────────────
    sources_block = (
        f"\t\t{sources_phase_id} /* Sources */ = {{\n"
        f"\t\t\tisa = PBXSourcesBuildPhase;\n"
        f"\t\t\tbuildActionMask = 2147483647;\n"
        f"\t\t\tfiles = (\n"
        f"\t\t\t\t{src_build_file_id} /* {SOURCE_BASENAME} in Sources */,\n"
        f"\t\t\t);\n"
        f"\t\t\trunOnlyForDeploymentPostprocessing = 0;\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXSourcesBuildPhase section \*/\n)",
        sources_block + r"\1",
        text,
        count=1,
    )

    # ─── 10. PBXTargetDependency ─────────────────────────────────
    dep_block = (
        f"\t\t{dep_id} /* PBXTargetDependency */ = {{\n"
        f"\t\t\tisa = PBXTargetDependency;\n"
        f"\t\t\ttarget = {runner_id} /* Runner */;\n"
        f"\t\t\ttargetProxy = {proxy_id} /* PBXContainerItemProxy */;\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End PBXTargetDependency section \*/\n)",
        dep_block + r"\1",
        text,
        count=1,
    )

    # ─── 11. XCBuildConfiguration entries ───────────────────────
    def make_cfg(id_: str, name: str, debug: bool) -> str:
        debug_format = "dwarf" if debug else "dwarf-with-dsym"
        opt_level = "0" if debug else "s"
        debug_info = "INCLUDE_SOURCE" if debug else "NO"
        active_arch = "YES" if debug else "NO"
        if debug:
            preproc = '"DEBUG=1",\n\t\t\t\t\t"$(inherited)",'
        else:
            preproc = '"$(inherited)",'

        body = (
            f"\t\t{id_} /* {name} */ = {{\n"
            f"\t\t\tisa = XCBuildConfiguration;\n"
            f"\t\t\tbuildSettings = {{\n"
            f"\t\t\t\tALWAYS_SEARCH_USER_PATHS = 0;\n"
            f"\t\t\t\tCLANG_CXX_LANGUAGE_STANDARD = \"gnu++20\";\n"
            f"\t\t\t\tCLANG_ENABLE_MODULES = YES;\n"
            f"\t\t\t\tCLANG_ENABLE_OBJC_ARC = YES;\n"
            f"\t\t\t\tCLANG_ENABLE_OBJC_WEAK = YES;\n"
            f"\t\t\t\tDEBUG_INFORMATION_FORMAT = {debug_format};\n"
            f"\t\t\t\tENABLE_STRICT_OBJC_MSGSEND = YES;\n"
            f"\t\t\t\tENABLE_TESTABILITY = YES;\n"
            f"\t\t\t\tGCC_C_LANGUAGE_STANDARD = gnu17;\n"
            f"\t\t\t\tGCC_DYNAMIC_NO_PIC = NO;\n"
            f"\t\t\t\tGCC_NO_COMMON_BLOCKS = YES;\n"
            f"\t\t\t\tGCC_OPTIMIZATION_LEVEL = {opt_level};\n"
            f"\t\t\t\tGCC_PREPROCESSOR_DEFINITIONS = (\n"
            f"\t\t\t\t\t{preproc}\n"
            f"\t\t\t\t);\n"
            f"\t\t\t\tGENERATE_INFOPLIST_FILE = YES;\n"
            f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = 13.0;\n"
            f"\t\t\t\tMTL_ENABLE_DEBUG_INFO = {debug_info};\n"
            f"\t\t\t\tMTL_FAST_MATH = YES;\n"
            f"\t\t\t\tONLY_ACTIVE_ARCH = {active_arch};\n"
            f"\t\t\t\tSDKROOT = iphoneos;\n"
            f"\t\t\t\tTARGETED_DEVICE_FAMILY = \"1,2\";\n"
            f"\t\t\t}};\n"
            f"\t\t\tname = {name};\n"
            f"\t\t}};\n"
        )
        return body

    configs = (
        make_cfg(config_debug_id, "Debug", True)
        + make_cfg(config_release_id, "Release", False)
        + make_cfg(config_profile_id, "Profile", False)
    )
    text = re.sub(
        r"(/\* End XCBuildConfiguration section \*/\n)",
        configs + r"\1",
        text,
        count=1,
    )

    # ─── 12. XCConfigurationList ────────────────────────────────
    cfg_list_block = (
        f"\t\t{config_list_id} /* Build configuration list for PBXNativeTarget "
        f"\"RunnerUITests\" */ = {{\n"
        f"\t\t\tisa = XCConfigurationList;\n"
        f"\t\t\tbuildConfigurations = (\n"
        f"\t\t\t\t{config_debug_id} /* Debug */,\n"
        f"\t\t\t\t{config_release_id} /* Release */,\n"
        f"\t\t\t\t{config_profile_id} /* Profile */,\n"
        f"\t\t\t);\n"
        f"\t\t\tdefaultConfigurationIsVisible = 0;\n"
        f"\t\t}};\n"
    )
    text = re.sub(
        r"(/\* End XCConfigurationList section \*/\n)",
        cfg_list_block + r"\1",
        text,
        count=1,
    )

    # ─── 13. Products group: include RunnerUITests.xctest ───────
    text = re.sub(
        r"(/\* Products \*/ = \{\s*isa = PBXGroup;\s*children = \(\n)((?:\s*\w{24} /\* [^*]+ \*/,\n)+)",
        lambda m: m.group(1) + m.group(2) + f"\t\t\t\t{prod_ref_id} /* RunnerUITests.xctest */,\n",
        text,
        count=1,
    )

    PROJECT_PATH.write_text(text)
    print(f"✓ injected RunnerUITests target into {PROJECT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())