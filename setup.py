import setuptools

with open('README.md') as file:
    readme = file.read()

setuptools.setup(
    name='torrentrss',
    version='0.5.1',
    license='MIT',
    description=('An RSS torrent fetcher. Matches entries with regexp, '
                 'keeps track of episode numbers, allows custom commands, '
                 'magnet links, and more.'),
    long_description=readme,
    author='Gregor Smith',
    author_email='gregor_smith@outlook.com',
    url='https://github.com/gregor-smith/torrentrss',
    packages=['torrentrss'],
    package_data={'torrentrss': ['config_schema.json']},
    entry_points={'console_scripts': ['torrentrss=torrentrss:main']},
    platforms='any',
    install_requires=['click', 'easygui', 'requests',
                      'feedparser', 'jsonschema'],
    classifiers=['Development Status :: 3 - Alpha',
                 'License :: OSI Approved :: MIT License',
                 'Operating System :: OS Independent',
                 'Programming Language :: Python :: 3',
                 'Programming Language :: Python :: 3.5',
                 'Topic :: Utilities'],
    test_suite='torrentrss.tests'
)
