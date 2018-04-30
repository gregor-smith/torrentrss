import setuptools

with open('README.md') as file:
    readme = file.read()

setuptools.setup(
    name='torrentrss',
    version='0.6',
    license='MIT',
    description=('An RSS torrent fetcher. Matches entries with regexp, '
                 'keeps track of episode numbers, allows custom commands, '
                 'and more.'),
    long_description=readme,
    author='Gregor Smith',
    author_email='gregor_smith@outlook.com',
    url='https://github.com/gregor-smith/torrentrss',
    packages=['torrentrss'],
    package_data={'torrentrss': ['config_schema.json']},
    entry_points={'console_scripts': ['torrentrss=torrentrss:main']},
    platforms='any',
    setup_requires=['pytest-runner'],
    install_requires=['click', 'requests', 'feedparser', 'jsonschema'],
    tests_require=['pytest'],
    classifiers=['Development Status :: 3 - Alpha',
                 'License :: OSI Approved :: MIT License',
                 'Operating System :: OS Independent',
                 'Programming Language :: Python :: 3',
                 'Programming Language :: Python :: 3.6',
                 'Topic :: Utilities']
)
