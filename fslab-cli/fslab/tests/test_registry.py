cd /home/claude && pip install pydantic --break-system-packages -q && python -c "
from registry import PlatformEntry, RegistryFile, MasterRegistry
import traceback

def expect_ok(label, data):
    try:
        p = PlatformEntry(**data)
        print(f'  OK  {label}')
    except Exception as e:
        print(f'  FAIL {label}: {e}')

def expect_err(label, data, code):
    try:
        p = PlatformEntry(**data)
        print(f'  FAIL {label}: expected error [{code}] but got none')
    except Exception as e:
        tag = f'[{code}]'
        msg = str(e)
        if tag in msg:
            print(f'  OK  {label} → caught {tag}')
        else:
            print(f'  FAIL {label}: expected [{code}] but got: {msg[:120]}')

base = dict(id='f2', label='AWS F2', config_package='firesim.midasexamples', config_class='DefaultF2Config')

print('--- REG-09: required_env_vars ---')
expect_ok('uppercase valid', {**base, 'required_env_vars': ['XILINX_XRT', 'MY_SDK_ROOT']})
expect_err('lowercase name', {**base, 'required_env_vars': ['xilinx_xrt']}, 'REG-09')
expect_err('hyphenated name', {**base, 'required_env_vars': ['MY-VAR']}, 'REG-09')

print()
print('--- REG-09 cross-field: $ENV{} in paths must be in required_env_vars ---')
expect_ok('env ref declared', {**base,
    'required_env_vars': ['XILINX_XRT'],
    'extra_include_dirs': ['\$ENV{XILINX_XRT}/include']})
expect_err('env ref undeclared', {**base,
    'extra_include_dirs': ['\$ENV{XILINX_XRT}/include']}, 'REG-09')

print()
print('--- REG-10: extra_libs ---')
expect_ok('bare names', {**base, 'extra_libs': ['fpga_mgmt', 'z', 'xrt_coreutil', 'stdc++']})
expect_err('-l prefix', {**base, 'extra_libs': ['-lfpga_mgmt']}, 'REG-10')
expect_err('empty entry', {**base, 'extra_libs': ['']}, 'REG-10')
expect_err('spaces in name', {**base, 'extra_libs': ['my lib']}, 'REG-10')

print()
print('--- REG-11: extra_include_dirs / extra_link_dirs ---')
expect_ok('absolute path', {**base, 'extra_include_dirs': ['/usr/include']})
expect_ok('cmake var ref', {**base, 'extra_include_dirs': ['\${PLATFORMS_DIR}/sdk/include']})
expect_ok('cmake env ref', {**base,
    'required_env_vars': ['XILINX_XRT'],
    'extra_include_dirs': ['\$ENV{XILINX_XRT}/include']})
expect_err('relative path', {**base, 'extra_include_dirs': ['sdk/include']}, 'REG-11')
expect_err('empty entry', {**base, 'extra_link_dirs': ['']}, 'REG-11')

print()
print('--- REG-12: extra_cxx_flags / extra_link_options ---')
expect_ok('valid flags', {**base, 'extra_cxx_flags': ['-O2', '-Wall'], 'extra_link_options': ['-Wl,-rpath,\$ORIGIN']})
expect_err('missing dash cxx', {**base, 'extra_cxx_flags': ['O2']}, 'REG-12')
expect_err('missing dash link', {**base, 'extra_link_options': ['Wl,-rpath,x']}, 'REG-12')
expect_err('empty flag', {**base, 'extra_cxx_flags': ['']}, 'REG-12')

print()
print('--- REG-13: cmake_fragment ---')
expect_ok('valid fragment', {**base, 'cmake_fragment': 'find_package(XRT REQUIRED)\ntarget_link_libraries(\${DRIVER_TARGET} PRIVATE XRT::xrt++)'})
expect_ok('empty fragment', {**base, 'cmake_fragment': ''})
expect_err('jinja2 expr leaked', {**base, 'cmake_fragment': 'target_link_libraries(\${DRIVER_TARGET} {{ some_var }})'}, 'REG-13')
expect_err('jinja2 stmt leaked', {**base, 'cmake_fragment': '{% if foo %}message(STATUS x){% endif %}'}, 'REG-13')
"