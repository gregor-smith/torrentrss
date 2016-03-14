import setuptools

setuptools.setup(
    name='torrentrss',
    description='torrentrss',
    version='0.4',
    author='Gregor Smith',
    url='https://github.com/gregor-smith/torrentrss',
    license='MIT',
    install_requires=['click', 'requests', 'feedparser', 'jsonschema'],
    packages=['torrentrss'],
    package_data={'torrentrss': ['config_schema.json']},
    entry_points={'console_scripts': ['torrentrss=torrentrss:main']}
)
